"""Bounded JSON-RPC transports for explicitly trusted local/remote integrations."""

import json
import os
import queue
import signal
import subprocess
import threading
import time

import httpx

from codaro.llm import RequestCancelled
from codaro.runtime import remaining_seconds

MAX_FRAME = 1_000_000


def check(cancelled, deadline):
    if cancelled is not None and cancelled.is_set():
        raise RequestCancelled("Integração cancelada.")
    remaining = remaining_seconds()
    if time.monotonic() >= deadline or remaining is not None and remaining <= 0:
        raise ValueError("Integração excedeu o tempo disponível.")


def environment(names=()):
    result = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "HOME", "TMPDIR")
        if key in os.environ
    }
    for name in names:
        if name in os.environ:
            result[name] = os.environ[name]
    return result


class StdioRPC:
    def __init__(self, argv, root, *, framing="lines", env_names=()):
        if (
            not isinstance(argv, list)
            or not 1 <= len(argv) <= 32
            or not all(
                isinstance(part, str) and 0 < len(part) <= 4096 and "\0" not in part
                for part in argv
            )
        ):
            raise ValueError("Comando da integração inválido.")
        self.framing = framing
        self.process = subprocess.Popen(
            argv,
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment(env_names),
            start_new_session=os.name == "posix",
        )
        self.queue = queue.Queue(maxsize=64)
        self.notifications = []
        self.sequence = 0
        self.closed = False
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_FRAME + 1)
                if not line:
                    raise ValueError("Integração encerrou o processo.")
                if len(line) > MAX_FRAME:
                    raise ValueError("Mensagem da integração excede 1 MB.")
                if self.framing == "headers":
                    headers = [line]
                    while headers[-1] not in {b"\r\n", b"\n"}:
                        if len(headers) > 16 or sum(map(len, headers)) > 8192:
                            raise ValueError("Cabeçalho RPC inválido.")
                        next_line = self.process.stdout.readline(8193)
                        if not next_line:
                            raise ValueError("Cabeçalho RPC incompleto.")
                        headers.append(next_line)
                    lengths = [
                        part.split(b":", 1)[1].strip()
                        for part in headers
                        if part.lower().startswith(b"content-length:")
                    ]
                    if len(lengths) != 1 or not lengths[0].isdigit():
                        raise ValueError("Tamanho RPC inválido.")
                    length = int(lengths[0])
                    if not 0 < length <= MAX_FRAME:
                        raise ValueError("Mensagem da integração excede 1 MB.")
                    line = self.process.stdout.read(length)
                    if len(line) != length:
                        raise ValueError("Mensagem RPC incompleta.")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Mensagem RPC inválida.")
                self.queue.put_nowait(value)
        except (OSError, ValueError, queue.Full, RecursionError):
            try:
                self.queue.put_nowait({"_failure": True})
            except queue.Full:
                pass

    def send(self, value, cancelled=None):
        raw = json.dumps(value, ensure_ascii=False).encode()
        if len(raw) > MAX_FRAME:
            raise ValueError("Solicitação RPC excede 1 MB.")
        raw = (
            f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw
            if self.framing == "headers"
            else raw + b"\n"
        )
        ready, failures = threading.Event(), []

        def write():
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                failures.append(exc)
            finally:
                ready.set()

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        deadline = time.monotonic() + 5
        try:
            while not ready.wait(0.05):
                check(cancelled, deadline)
            if failures:
                raise ValueError("Integração encerrou a entrada RPC.") from failures[0]
        except (ValueError, RequestCancelled):
            self.close()
            raise

    def notify(self, method, params=None):
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def receive(self, timeout=0.05):
        try:
            value = self.queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if value.get("_failure"):
            raise ValueError("Resposta RPC inválida ou processo encerrado.")
        if "method" in value:
            if "id" in value:
                self.send(
                    {
                        "jsonrpc": "2.0",
                        "id": value["id"],
                        "error": {"code": -32601, "message": "Client method unsupported"},
                    }
                )
            elif len(self.notifications) < 100:
                self.notifications.append(value)
            return None
        return value

    def request(self, method, params=None, *, cancelled=None, timeout=30):
        deadline = time.monotonic() + timeout
        check(cancelled, deadline)
        self.sequence += 1
        identifier = self.sequence
        self.send(
            {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}},
            cancelled=cancelled,
        )
        while True:
            check(cancelled, deadline)
            response = self.receive()
            if response is None:
                continue
            if response.get("id") != identifier or response.get("jsonrpc") != "2.0":
                raise ValueError("Resposta RPC não corresponde à solicitação.")
            if response.get("error"):
                raise ValueError("Integração retornou erro RPC.")
            if "result" not in response:
                raise ValueError("Resposta RPC sem resultado.")
            return response["result"]

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if os.name == "posix":
                os.killpg(self.process.pid, signal.SIGKILL)
            elif self.process.poll() is None:
                self.process.kill()
        except ProcessLookupError:
            pass
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        self.reader.join(timeout=1)


class HttpRPC:
    """MCP Streamable HTTP (JSON/SSE); legacy SSE transport is intentionally unsupported."""

    def __init__(self, url, *, token="", tls_insecure=False, transport=None):
        self.url = url
        self.client = httpx.Client(
            verify=not tls_insecure,
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(5, connect=5),
        )
        self.headers = {"Accept": "application/json, text/event-stream"}
        if token:
            self.headers["Authorization"] = "Bearer " + token
        self.sequence = 0
        self.protocol = None

    def exchange(self, payload, cancelled, timeout=30):
        deadline = time.monotonic() + timeout
        check(cancelled, deadline)
        try:
            with self.client.stream(
                "POST",
                self.url,
                json=payload,
                headers=self.headers,
                timeout=max(0.1, min(5, timeout, remaining_seconds() or 5)),
            ) as reply:
                if reply.status_code == 202 and "id" not in payload:
                    return None
                if reply.status_code != 200:
                    raise ValueError(f"Integração HTTP retornou {reply.status_code}.")
                if session := reply.headers.get("mcp-session-id"):
                    if len(session) > 256 or "\n" in session or "\r" in session:
                        raise ValueError("Sessão MCP inválida.")
                    self.headers["Mcp-Session-Id"] = session
                raw = bytearray()
                for chunk in reply.iter_bytes():
                    check(cancelled, deadline)
                    raw.extend(chunk)
                    if len(raw) > MAX_FRAME:
                        raise ValueError("Resposta MCP excede 1 MB.")
                    if "text/event-stream" in reply.headers.get("content-type", ""):
                        # A response may leave the stream open after its event.
                        for event in bytes(raw).replace(b"\r\n", b"\n").split(b"\n\n")[:-1]:
                            data = b"\n".join(
                                line[5:].lstrip()
                                for line in event.splitlines()
                                if line.startswith(b"data:")
                            )
                            if data:
                                value = json.loads(data)
                                if isinstance(value, dict) and value.get("id") == payload.get("id"):
                                    return value
                if "id" not in payload and not raw:
                    return None
                return json.loads(raw)
        except (httpx.HTTPError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Falha de transporte MCP HTTP.") from exc

    def request(self, method, params=None, *, cancelled=None, timeout=30):
        self.sequence += 1
        reply = self.exchange(
            {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params or {}},
            cancelled,
            timeout,
        )
        if (
            not isinstance(reply, dict)
            or reply.get("jsonrpc") != "2.0"
            or reply.get("id") != self.sequence
            or "result" not in reply
            or reply.get("error")
        ):
            raise ValueError("Resposta MCP inválida ou com erro.")
        return reply["result"]

    def notify(self, method, params=None):
        self.exchange({"jsonrpc": "2.0", "method": method, "params": params or {}}, None)

    def close(self):
        self.client.close()

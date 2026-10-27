"""Exercise the real Go terminal app against a local, deterministic Ollama mock.

Unix only. Run after `go build -o bin/codaro-go ./cmd/codaro`:
    python3 scripts/smoke_terminal.py
No external network, credentials or real model inference are used.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    requests = []

    class Mock(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"models": [{"name": "fixture-tools"}]}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            if self.path.endswith("/show"):
                self.wfile.write(json.dumps({"model_info": {
                    "general.architecture": "llama", "llama.context_length": 131072},
                    "capabilities": ["completion", "tools"]}).encode())
                return
            requests.append(body)
            assert body["options"]["num_ctx"] == 131072, body["options"]
            last = body["messages"][-1]
            name = last.get("tool_name",last.get("name"))
            if last["role"]=="tool" and not name:
                name=body["messages"][-2].get("tool_calls",[{}])[-1].get("function",{}).get("name")
            definitions = {t["function"]["name"] for t in body["tools"]}
            assert {"ask_user_question", "update_plan"} <= definitions

            def event(message, done=False):
                self.wfile.write((json.dumps({"message": message, "done": done}) + "\n").encode())
                self.wfile.flush()

            def call(name, args):
                event({"role": "assistant", "thinking": "Consultando as fontes reais do projeto."})
                event({"role": "assistant", "tool_calls": [{"function": {
                    "name": name, "arguments": args}}]}, True)

            if last["role"] == "user":
                call("update_plan", {"tasks": [
                    {"title": "Investigar código", "status": "in_progress"},
                    {"title": "Preparar recomendações", "status": "pending"}]})
            elif name == "update_plan" and "in_progress" in last["content"]:
                call("ask_user_question", {"question": "Qual área você quer revisar?",
                                          "options": ["Interface", "Provedores"]})
            elif name == "ask_user_question":
                assert json.loads(last["content"])["answer"] == "Provedores"
                call("read_lines", {"path": "main.go", "start": 1, "end": 2})
            elif name == "read_lines":
                assert "func main" in last["content"]
                call("update_plan", {"tasks": [
                    {"title": "Investigar código", "status": "completed"},
                    {"title": "Preparar recomendações", "status": "completed"}]})
            else:
                for word in ["Revisei ", "main.go. ", "Fluxo concluído."]:
                    event({"role": "assistant", "content": word})
                    time.sleep(.04)
                event({"role": "assistant"}, True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="codaro-terminal-") as directory:
            base = Path(directory)
            root = base / "project"
            root.mkdir()
            (root / "main.go").write_text("package main\nfunc main() {}\n")
            master, slave = pty.openpty()
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            env = os.environ.copy()
            for key in list(env):
                if key.startswith("CODARO_GO_"):
                    env.pop(key)
            env.update(TERM="xterm-256color", CODARO_GO_CONFIG_DIR=str(base / "settings"),
                       CODARO_GO_PROVIDER="ollama", CODARO_GO_MODEL="fixture-tools",
                       CODARO_GO_BASE_URL=f"http://127.0.0.1:{server.server_port}")
            binary = Path(__file__).resolve().parents[1] / "bin/codaro-go"
            app = subprocess.Popen([str(binary), str(root)], stdin=slave, stdout=slave,
                                   stderr=slave, env=env, start_new_session=True)
            os.close(slave)
            transcript = bytearray()

            def read_for(seconds):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if select.select([master], [], [], .02)[0]:
                        try:
                            transcript.extend(os.read(master, 65536))
                        except OSError:
                            return

            def until(predicate, timeout=10):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    read_for(.03)
                    if predicate():
                        return
                raise AssertionError("Terminal did not reach expected state: "+bytes(transcript[-6000:]).decode(errors="replace"))

            try:
                until(lambda: b"Codaro" in transcript)
                os.write(master, "Analise o projeto\r".encode())
                until(lambda: "Sua decisão".encode() in transcript)
                os.write(master, b"\x1b[B\r")
                trace = base / "settings/runs" / hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16] / "prompt.json"
                until(trace.exists)
                result = json.loads(trace.read_text())
                assert result["status"] == "completed", result["status"]
                assert [t["name"] for t in result["tools"]] == [
                    "update_plan", "ask_user_question", "read_lines", "update_plan"]
                until(lambda: list((trace.parent / "sessions").glob("*.json")))
                read_for(.1)
                os.write(master, b"/new\r")
                read_for(.15)
                os.write(master, b"/sessions\r")
                until(lambda: b"Conversas deste projeto" in transcript)
                read_for(.1)
                os.write(master, b"\r")
                until(lambda: b"Conversa retomada" in transcript)
                assert len(requests) == 5, "Resume unexpectedly called the model"
                started = time.monotonic()
                os.write(master, b"\x11")
                while app.poll() is None and time.monotonic() - started < 3:
                    read_for(.02)
                app.wait(timeout=1)
                assert app.returncode == 0
                print(json.dumps({"terminal": [100, 30], "provider": "local HTTP mock; no real inference",
                                  "requests": len(requests), "context_from_api": 131072,
                                  "question_answered": True, "plan_updated": True,
                                  "session_resumed_without_execution": True,
                                  "quit_ms": round((time.monotonic() - started) * 1000), "exit": app.returncode}))
            finally:
                if app.poll() is None:
                    app.kill()
                    app.wait()
                os.close(master)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

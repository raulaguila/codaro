"""Cancellable network I/O behind the synchronous provider interface.

Parsing, trace writes and callbacks stay on the caller's thread. The worker only
owns HTTP I/O, with bounded backpressure and an async transport that can interrupt
connect, response headers and body reads without waiting for the read timeout.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from contextlib import contextmanager

import httpx

from codaro.llm.streaming import check_cancelled
from codaro.runtime import remaining_seconds


class _Exchange(httpx.SyncByteStream):
    def __init__(self, request, *, verify, cancelled):
        self.request, self.verify, self.cancelled = request, verify, cancelled
        self.events = queue.Queue(maxsize=2)
        self.stopped = threading.Event()
        self.body = request.read()
        self.thread = threading.Thread(target=self._run, name="codaro-http", daemon=True)
        self.thread.start()

    def _run(self):
        async def publish(value):
            while not self.stopped.is_set():
                try:
                    self.events.put_nowait(value)
                    return
                except queue.Full:
                    await asyncio.sleep(0.01)

        async def run():
            try:
                if self.stopped.is_set():
                    return
                # AsyncClient preserves proxy and CA environment settings. Explicit
                # per-request timeouts come from the outer synchronous client.
                async with httpx.AsyncClient(verify=self.verify) as client:
                    request = client.build_request(
                        self.request.method,
                        self.request.url,
                        headers=self.request.headers,
                        content=self.body,
                        extensions=self.request.extensions,
                    )
                    response = await client.send(request, stream=True)
                    try:
                        await publish(("headers", response))
                        async for part in response.aiter_raw():
                            await publish(("body", part))
                    finally:
                        await response.aclose()
                await publish(("done", None))
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                await publish(("error", exc))

        async def supervise():
            task = asyncio.create_task(run())
            try:
                while not task.done() and not self.stopped.is_set():
                    await asyncio.sleep(0.025)
            finally:
                if not task.done():
                    task.cancel()
                await task

        try:
            asyncio.run(supervise())
        except Exception as exc:
            if not self.stopped.is_set():
                self.events.put(("error", exc))

    def receive(self):
        while True:
            check_cancelled(self.cancelled)
            try:
                kind, value = self.events.get(timeout=0.05)
            except queue.Empty:
                continue
            check_cancelled(self.cancelled)
            if kind == "error":
                raise value
            return kind, value

    def __iter__(self):
        while True:
            kind, value = self.receive()
            if kind == "done":
                return
            yield value

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.thread.join(timeout=1)


class CancellableTransport(httpx.BaseTransport):
    def __init__(self, *, verify=True, cancelled=None):
        self.verify, self.cancelled = verify, cancelled

    def handle_request(self, request):
        check_cancelled(self.cancelled)
        exchange = _Exchange(request, verify=self.verify, cancelled=self.cancelled)
        try:
            kind, response = exchange.receive()
            if kind != "headers":
                raise httpx.RemoteProtocolError("Resposta HTTP sem cabeçalhos.")
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=exchange,
                extensions={
                    key: value
                    for key, value in response.extensions.items()
                    if key in {"http_version", "reason_phrase"}
                },
            )
        except BaseException:
            exchange.close()
            raise


@contextmanager
def model_client(settings, transport=None, cancelled=None):
    check_cancelled(cancelled)
    remaining = remaining_seconds()
    timeout = min(settings.timeout, remaining) if remaining is not None else settings.timeout
    with httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
        transport=transport
        if transport is not None
        else CancellableTransport(verify=not settings.tls_insecure, cancelled=cancelled),
        verify=not settings.tls_insecure,
    ) as client:
        yield client

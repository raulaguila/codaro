"""Real local sockets exercise blocking reads, unlike MockTransport contracts."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from codaro.llm import ModelError, RequestCancelled, Settings, create_provider
from codaro.llm.catalog import ModelCatalog
from codaro.runtime import request_deadline


@pytest.fixture
def async_network(monkeypatch):
    """Exercise the production async bridge even in executors without sockets."""
    entered, closed = threading.Event(), threading.Event()
    state = {"stall": "headers"}
    client_type = httpx.AsyncClient

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            if state["stall"] == "body":
                entered.set()
                await asyncio.Event().wait()
            yield b'{"choices":[{"message":{"content":"ok"}}]}'

        async def aclose(self):
            closed.set()

    async def handle(request):
        if state["stall"] == "headers":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        return httpx.Response(200, stream=Body())

    def client(**kwargs):
        return client_type(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr("codaro.llm.transport.httpx.AsyncClient", client)
    return state, entered, closed


@pytest.mark.parametrize("style", ["openai", "ollama", "anthropic"])
@pytest.mark.parametrize("stall", ["headers", "body"])
def test_async_bridge_cancels_io_and_closes_connection(async_network, style, stall):
    state, entered, closed = async_network
    state["stall"] = stall
    provider = create_provider(Settings("http://example.test", "model", api_style=style))
    cancelled = threading.Event()

    def cancel():
        if entered.wait(3):
            cancelled.set()

    thread = threading.Thread(target=cancel, daemon=True)
    thread.start()
    with pytest.raises(RequestCancelled):
        provider.stream([{"role": "user", "content": "Hello"}], cancelled=cancelled)
    thread.join(2)
    assert closed.is_set()
    assert not any(t.name == "codaro-http" for t in threading.enumerate())


@pytest.mark.parametrize("stall", ["headers", "body"])
def test_async_bridge_deadline_stops_headers_and_body(async_network, stall):
    state, _, closed = async_network
    state["stall"] = stall
    started = time.monotonic()
    token = request_deadline.set(lambda: started + 0.15)
    try:
        provider = create_provider(Settings("http://example.test", "model"))
        with pytest.raises(ModelError, match="Prazo"):
            provider.complete([{"role": "user", "content": "Hello"}])
        assert closed.is_set()
    finally:
        request_deadline.reset(token)


def test_async_bridge_success(async_network):
    state, _, closed = async_network
    state["stall"] = "none"
    provider = create_provider(Settings("http://example.test", "model"))
    assert provider.complete([{"role": "user", "content": "Hello"}])["content"] == "ok"
    assert closed.is_set()


@pytest.mark.parametrize("stall", ["headers", "body"])
def test_catalog_cancellation_closes_active_request(async_network, stall):
    state, entered, closed = async_network
    state["stall"] = stall
    cancelled = threading.Event()
    catalog = ModelCatalog(
        {"kind": "custom", "base_url": "http://example.test", "api_key": "", "tls_insecure": False},
        cancelled=cancelled,
    )

    def cancel():
        if entered.wait(3):
            cancelled.set()

    thread = threading.Thread(target=cancel, daemon=True)
    thread.start()
    with pytest.raises(RequestCancelled):
        catalog.list()
    thread.join(2)
    assert closed.is_set()


def test_async_bridge_delivers_stream_before_completion_and_callbacks_stay_on_caller(monkeypatch):
    client_type = httpx.AsyncClient
    delivered = threading.Event()
    caller = threading.get_ident()
    chunks = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            while not delivered.is_set():
                await asyncio.sleep(0.01)
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b"data: [DONE]\n\n"

    async def handle(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

    monkeypatch.setattr(
        "codaro.llm.transport.httpx.AsyncClient",
        lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(handle)),
    )

    def on_delta(text):
        assert threading.get_ident() == caller
        chunks.append(text)
        delivered.set()

    provider = create_provider(Settings("http://example.test", "model"))
    token = request_deadline.set(lambda: started + 3)
    started = time.monotonic()
    try:
        answer = provider.stream([{"role": "user", "content": "Hello"}], on_delta=on_delta)
        assert answer["content"] == "".join(chunks) == "hello"
    finally:
        request_deadline.reset(token)


@pytest.fixture
def server(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    state = {"stall": "headers"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            if state["stall"] == "headers":
                release.wait(5)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if state["stall"] == "body":
                release.wait(5)
            try:
                self.wfile.write(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("O ambiente bloqueia sockets locais; execute com acesso de rede.")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    # Exempt only this loopback fixture, preserving the inherited proxy policy.
    import os

    monkeypatch.setenv("NO_PROXY", os.environ.get("NO_PROXY", "") + ",127.0.0.1")
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", state, entered, release
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


@pytest.mark.parametrize("style", ["openai", "ollama", "anthropic"])
@pytest.mark.parametrize("stall", ["headers", "body"])
def test_cancellation_interrupts_network_reads_without_waiting_for_timeout(server, style, stall):
    url, state, entered, release = server
    state["stall"] = stall
    provider = create_provider(Settings(url, "model", api_style=style, timeout=30))
    cancelled = threading.Event()
    deltas, reasoning = [], []

    def cancel():
        assert entered.wait(3)
        time.sleep(0.05)
        cancelled.set()

    timer = threading.Thread(target=cancel, daemon=True)
    timer.start()
    started = time.monotonic()
    with pytest.raises(RequestCancelled):
        provider.stream(
            [{"role": "user", "content": "Hello"}],
            on_delta=deltas.append,
            on_reasoning=reasoning.append,
            cancelled=cancelled,
        )
    assert time.monotonic() - started < 3
    release.set()
    timer.join(2)
    assert deltas == reasoning == []
    assert not any(t.name == "codaro-http" for t in threading.enumerate())


@pytest.mark.parametrize("stall", ["headers", "body"])
def test_deadline_interrupts_network_reads_and_cleans_worker(server, stall):
    url, state, _, _ = server
    state["stall"] = stall
    provider = create_provider(Settings(url, "model", timeout=30))
    token = request_deadline.set(lambda: started + 0.4)
    started = time.monotonic()
    try:
        with pytest.raises(ModelError, match="Prazo"):
            provider.stream([{"role": "user", "content": "Hello"}])
        assert time.monotonic() - started < 2
        assert not any(t.name == "codaro-http" for t in threading.enumerate())
    finally:
        request_deadline.reset(token)


def test_real_http_response_completes_and_releases_worker(server):
    url, state, _, _ = server
    state["stall"] = "none"
    provider = create_provider(Settings(url, "model"))
    deltas = []
    result = provider.stream([{"role": "user", "content": "Hello"}], on_delta=deltas.append)
    assert result["content"] == "ok" and deltas == ["ok"]
    assert not any(t.name == "codaro-http" for t in threading.enumerate())

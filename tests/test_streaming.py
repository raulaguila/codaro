import json
import threading

import httpx
import pytest

from codaro.provider import (
    MAX_STREAM_BYTES,
    ModelError,
    OpenAICompatible,
    RequestCancelled,
    Settings,
)


def chunk(delta=None, reason=None):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": reason}]}


def encode(events, done=True):
    text = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events)
    if done:
        text += "data: [DONE]\n\n"
    return text.encode()


class Bytes(httpx.SyncByteStream):
    def __init__(self, parts):
        self.parts = parts
        self.closed = False

    def __iter__(self):
        yield from self.parts

    def close(self):
        self.closed = True


def model(parts):
    byte_stream = Bytes(parts)

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=byte_stream
        )

    return OpenAICompatible(
        Settings("https://example.test/v1", "test"), httpx.MockTransport(handler)
    ), byte_stream


def test_stream_text_and_unicode_split_across_bytes():
    raw = encode(
        [
            chunk({"role": "assistant", "content": "Olá"}),
            chunk({"content": ", mundo!"}),
            chunk(reason="stop"),
        ]
    )
    provider, stream = model([raw[i : i + 1] for i in range(len(raw))])
    received = []
    result = provider.stream([], on_delta=received.append)
    assert result["content"] == "Olá, mundo!"
    assert "".join(received) == result["content"]
    assert stream.closed


def test_stream_reassembles_tool_names_and_arguments():
    raw = encode(
        [
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read_", "arguments": '{"pa'},
                        }
                    ]
                }
            ),
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "function": {
                                "name": "symbol",
                                "arguments": 'th":"auth.py","symbol":"f"}',
                            },
                        }
                    ]
                }
            ),
            chunk(reason="tool_calls"),
        ]
    )
    provider, _ = model([raw])
    result = provider.stream([])
    tool = result["tool_calls"][0]
    assert tool["id"] == "call-1"
    assert tool["function"]["name"] == "read_symbol"
    assert json.loads(tool["function"]["arguments"]) == {"path": "auth.py", "symbol": "f"}


def test_stream_delivers_content_before_response_finishes():
    received = []

    def parts():
        yield encode([chunk({"content": "Primeiro"})], done=False)
        assert "".join(received) == "Primeiro"
        yield encode([chunk({"content": " e segundo."}), chunk(reason="stop")])

    provider, _ = model(parts())
    provider.stream([], on_delta=received.append)
    assert "".join(received) == "Primeiro e segundo."


def test_stream_cancellation_closes_connection():
    cancelled = threading.Event()
    received = []

    def receive(text):
        received.append(text)
        cancelled.set()

    raw = encode(
        [chunk({"content": "Parcial"}), chunk({"content": " restante"}), chunk(reason="stop")]
    )
    provider, stream = model([raw])
    with pytest.raises(RequestCancelled):
        provider.stream([], on_delta=receive, cancelled=cancelled)
    assert stream.closed
    assert "".join(received) == "Parcial"


def test_premature_eof_is_not_a_complete_answer():
    provider, _ = model([encode([chunk({"content": "Parcial"})], done=False)])
    with pytest.raises(ModelError, match="interrompida"):
        provider.stream([])


def test_stream_can_finish_without_done_when_finish_reason_present():
    provider, _ = model([encode([chunk({"content": "OK"}), chunk(reason="stop")], done=False)])
    assert provider.stream([])["content"] == "OK"


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {"error": {"message": "private-server-error"}},
        {"choices": [{}]},
        chunk({"content": []}),
        chunk({"role": "system", "content": "bad"}),
        chunk({"tool_calls": "bad"}),
        chunk({"tool_calls": [{"index": True}]}),
        chunk({"tool_calls": [{"index": 8}]}),
        chunk({"tool_calls": [{"index": 0, "function": []}]}),
    ],
)
def test_malformed_stream_events_fail_cleanly(event):
    provider, _ = model([encode([event])])
    with pytest.raises(ModelError) as failure:
        provider.stream([])
    assert "private-server-error" not in str(failure.value)


def test_stream_bounds_content_and_transport_bytes():
    provider, _ = model([encode([chunk({"content": "x" * 16_001})])])
    with pytest.raises(ModelError, match="limite"):
        provider.stream([])
    provider, _ = model([b":" * (MAX_STREAM_BYTES + 1)])
    with pytest.raises(ModelError, match="2 MB"):
        provider.stream([])


def test_stream_bounds_tool_arguments():
    provider, _ = model(
        [
            encode(
                [
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c",
                                    "function": {"name": "list_files", "arguments": "x" * 64001},
                                }
                            ]
                        }
                    )
                ]
            )
        ]
    )
    with pytest.raises(ModelError, match="Argumentos"):
        provider.stream([])


def test_stream_usage_and_sse_comments_are_ignored():
    raw = b": heartbeat\n\n" + encode(
        [chunk({"content": "OK"}), chunk(reason="stop"), {"choices": [], "usage": {}}]
    )
    provider, _ = model([raw])
    assert provider.stream([])["content"] == "OK"


def test_stream_handles_crlf_and_multiline_data():
    event = json.dumps(chunk({"content": "OK"}), indent=2)
    raw = (
        "".join("data: " + line + "\r\n" for line in event.splitlines())
        + "\r\ndata: [DONE]\r\n\r\n"
    )
    provider, _ = model([raw.encode()])
    assert provider.stream([])["content"] == "OK"


def test_stream_reports_output_truncation():
    provider, _ = model([encode([chunk({"content": "partial"}), chunk(reason="length")])])
    with pytest.raises(ModelError, match="limite de saída"):
        provider.stream([])


def test_stream_accepts_json_fallback_from_compatible_server():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Fallback"}}]})

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "test"), httpx.MockTransport(handler)
    )
    received = []
    assert provider.stream([], on_delta=received.append)["content"] == "Fallback"
    assert received == ["Fallback"]


def test_stream_rejects_content_after_finish():
    provider, _ = model(
        [encode([chunk({"content": "OK"}), chunk(reason="stop"), chunk({"content": "bad"})])]
    )
    with pytest.raises(ModelError, match="após concluir"):
        provider.stream([])


def test_stream_rejects_unknown_finish_reason():
    provider, _ = model([encode([chunk({"content": "OK"}), chunk(reason="unknown")])])
    with pytest.raises(ModelError, match="conclusão"):
        provider.stream([])


def test_stream_disconnect_is_not_retried_after_partial_output():
    attempts = []
    received = []

    def chunks():
        yield encode([chunk({"content": "partial"})], done=False)
        raise httpx.ReadError("disconnected")

    def handler(request):
        attempts.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Bytes(chunks())
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "test"), httpx.MockTransport(handler)
    )
    with pytest.raises(ModelError):
        provider.stream([], on_delta=received.append)
    assert len(attempts) == 1
    assert received == ["partial"]


def test_reasoning_channel_is_not_rendered_as_answer():
    provider, _ = model(
        [
            encode(
                [
                    chunk({"reasoning_content": "Internal model analysis"}),
                    chunk({"content": "Resposta final."}),
                    chunk(reason="stop"),
                ]
            )
        ]
    )
    received = []
    answer = provider.stream([], on_delta=received.append)
    assert answer["content"] == "Resposta final."
    assert "".join(received) == "Resposta final."


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
def test_reasoning_stream_uses_separate_optional_callback(field):
    provider, _ = model(
        [
            encode(
                [
                    chunk({field: "Nota provisória."}),
                    chunk({"content": "Final."}),
                    chunk(reason="stop"),
                ]
            )
        ]
    )
    reasoning, content = [], []
    answer = provider.stream([], on_delta=content.append, on_reasoning=reasoning.append)
    assert reasoning == ["Nota provisória."]
    assert "".join(content) == answer["content"] == "Final."
    assert "reasoning" not in answer and "reasoning_content" not in answer


def test_reasoning_callback_can_cancel_before_response_content():
    provider, _ = model(
        [
            encode(
                [
                    chunk({"reasoning_content": "Nota provisória."}),
                    chunk({"content": "Final."}),
                    chunk(reason="stop"),
                ]
            )
        ]
    )
    cancelled, content = threading.Event(), []
    with pytest.raises(RequestCancelled):
        provider.stream(
            [],
            on_delta=content.append,
            cancelled=cancelled,
            on_reasoning=lambda _: cancelled.set(),
        )
    assert not content

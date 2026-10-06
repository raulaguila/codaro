import json

import httpx

from codaro.agent import Agent
from codaro.provider import OpenAICompatible, Settings
from codaro.repository import Repository


def test_agent_roundtrip_through_openai_http_contract(tmp_path):
    (tmp_path / "auth.py").write_text("def can_edit(user):\n    return user.is_admin\n")
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            name, args = "search_code", {"query": "can_edit"}
        elif len(requests) == 2:
            tool = payload["messages"][-1]
            assert tool["role"] == "tool"
            assert json.loads(tool["content"])["results"][0]["symbol"] == "can_edit"
            name, args = "read_symbol", {"path": "auth.py", "symbol": "can_edit"}
        else:
            tool = payload["messages"][-1]
            assert "return user.is_admin" in json.loads(tool["content"])["content"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "auth.py:2 verifica se o usuário é administrador.",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"call-{len(requests)}",
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "test"), httpx.MockTransport(handle)
    )
    answer = Agent(Repository(tmp_path), provider).ask("Quem pode editar?")
    assert "auth.py:2" in answer
    assert len(requests) == 3


def test_agent_streaming_tool_roundtrip(tmp_path):
    (tmp_path / "auth.py").write_text("def can_edit(user):\n    return user.is_admin\n")
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        assert payload["stream"]
        requests.append(payload)
        if len(requests) == 1:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_symbol",
                            "arguments": '{"path":"auth.py","symbol":"can_edit"}',
                        },
                    }
                ]
            }
            reason = "tool_calls"
        else:
            assert "return user.is_admin" in payload["messages"][-1]["content"]
            delta = {"content": "**Verificação:** `auth.py:2` consulta `user.is_admin`."}
            reason = "stop"
        events = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
        ]
        body = (
            "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    model = OpenAICompatible(
        Settings("https://example.test/v1", "test"), httpx.MockTransport(handle)
    )
    received = []
    agent = Agent(Repository(tmp_path), model)
    answer = agent.ask("Quem pode editar?", on_delta=received.append)
    assert "".join(received) == answer
    assert "auth.py:2" in answer
    assert len(requests) == 2
    assert agent.turns[-1][-1]["content"] == answer


def test_quoted_integer_tool_call_roundtrip_returns_actual_files(tmp_path):
    (tmp_path / "auth.py").write_text("x = 1\n")
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            assert payload["tool_choice"] == "auto"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "Vou listar os arquivos.",
                                "tool_calls": [
                                    {
                                        "id": "list-1",
                                        "type": "function",
                                        "function": {
                                            "name": "list_files",
                                            "arguments": '{"limit":"10","offset":"0"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        tool = payload["messages"][-1]
        assert tool["role"] == "tool"
        assert tool["tool_call_id"] == "list-1"
        assert json.loads(tool["content"])["files"] == ["auth.py"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Arquivo: auth.py."}}]}
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "test"), httpx.MockTransport(handle)
    )
    agent = Agent(Repository(tmp_path), provider)
    assert (
        agent.ask("Quais arquivos estão no diretório atual?", on_delta=lambda _: None)
        == "Arquivo: auth.py."
    )
    assert len(requests) == 2

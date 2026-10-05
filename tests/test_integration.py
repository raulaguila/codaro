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

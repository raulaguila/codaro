import json

import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent
from codaro.llm import ContextCapacityError, Settings
from codaro.repository import Repository


def test_empty_response_recovers_without_repeating_tools(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')\n")
    model = FakeModel(
        [
            call("read_lines", {"path": "app.py", "start": 1, "end": 1}),
            {"content": None},
            {"content": "O projeto imprime hello."},
        ]
    )
    answer = Agent(Repository(tmp_path), model).ask("Leia app.py e explique.")
    assert answer == "O projeto imprime hello."
    assert len(model.requests) == 3
    assert model.requests[-1][1] is None
    assert "O orçamento de investigação terminou" not in model.requests[-1][0][0]["content"]
    results = [m for m in model.requests[-1][0] if m["role"] == "tool"]
    assert len(results) == 1
    assert "hello" in results[0]["content"]
    trace = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert any(t.get("outcome") == "empty_response_recovery" for t in trace["turns"])


def test_empty_response_recovery_is_bounded(tmp_path):
    model = FakeModel([{"content": ""}] * 3)
    with pytest.raises(ContextCapacityError, match="progresso foi salvo"):
        Agent(Repository(tmp_path), model).ask("Olá")
    assert len(model.requests) == 3


def test_tool_volume_stops_with_useful_result_before_exhaustion(tmp_path):
    (tmp_path / "app.py").write_text("\n".join(f"# {i} " + "x" * 100 for i in range(120)))
    model = FakeModel(
        [
            call("read_lines", {"path": "app.py", "start": 1, "end": 120}),
            {"content": "Análise parcial com evidências."},
        ]
    )
    Agent(Repository(tmp_path), model, tool_budget=6000).ask("Analise app.py")
    assert model.requests[-1][1] is None
    result = json.loads(next(m["content"] for m in model.requests[-1][0] if m["role"] == "tool"))
    assert result["content"] and result["truncated"]
    assert "error" not in result


def test_search_results_omitted_by_budget_are_not_reported_as_no_matches():
    result = Agent.fit_result({"results": [{"preview": "x" * 5000}]}, 300)
    assert result["results"] == []
    assert result["omitted_results"] == 1
    assert "encontrados" in result["notice"]


def test_context_char_ceiling_scales_but_explicit_limit_is_preserved(tmp_path):
    model = FakeModel([])
    model.settings = Settings("http://localhost/v1", "test", context_window=131072)
    assert Agent(Repository(tmp_path), model).context_budget > 64000
    assert Agent(Repository(tmp_path), model, context_budget=64000).context_budget == 64000


def test_reasoning_only_sse_recovers_as_final_text(tmp_path):
    import httpx
    from test_streaming import chunk, encode

    from codaro.llm import OpenAICompatible

    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        events = (
            [chunk({"reasoning": "Nota intermediária."}), chunk(reason="stop")]
            if len(requests) == 1
            else [chunk({"content": "Conclusão disponível."}), chunk(reason="stop")]
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=encode(events)
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "test"), httpx.MockTransport(handler)
    )
    text, reasoning = [], []
    answer = Agent(Repository(tmp_path), provider).ask(
        "Olá", on_delta=text.append, on_reasoning=reasoning.append
    )
    assert answer == "Conclusão disponível."
    assert "".join(text) == answer
    assert reasoning == ["Nota intermediária."]
    assert len(requests) == 2 and "tools" not in requests[-1]

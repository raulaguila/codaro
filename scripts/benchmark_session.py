"""Opt-in multi-turn read-only model benchmark, retaining per-turn measured reports."""

import argparse
import copy
import json
import time
from pathlib import Path

from codaro.agent import Agent
from codaro.features import DEFAULTS
from codaro.llm import ModelError, Settings, create_provider
from codaro.repository import Repository
from codaro.trace import atomic_write
from codaro.trace_metrics import evaluation_metrics

QUESTIONS = [
    "Explique a estrutura deste projeto e seus pontos de entrada. Cite arquivos consultados.",
    "Identifique um componente concreto da implementação que mereça melhoria e explique por quê.",
    "Leia os testes relacionados a esse componente e identifique uma lacuna de cobertura.",
    "Retome a melhoria sugerida anteriormente e detalhe um plano sem modificar arquivos.",
    "Revise o plano considerando compatibilidade e tratamento de falhas.",
    "Conclua com a melhoria escolhida, evidências, plano e limites do que foi verificado.",
]


def benchmark(repo, provider, rounds=1):
    features = copy.deepcopy(DEFAULTS)
    features.update(semantic_compaction=True, exploration=False, mcp={}, plugins={})
    agent = Agent(Repository(repo), provider, mode="ask", persist_memory=False, features=features)
    results = []
    for number, question in enumerate(QUESTIONS * rounds, 1):
        started = time.monotonic()
        record = {"turn": number, "question": question, "completed": False}
        try:
            answer = agent.ask(question)
            record.update(answer=answer, completed=True)
        except (ModelError, ValueError, OSError) as exc:
            record["error"] = str(exc)
        trace = json.loads(
            (Path(repo).resolve() / ".codaro/prompt.json").read_text(encoding="utf-8")
        )
        record.update(
            duration_ms=round((time.monotonic() - started) * 1000),
            metrics=evaluation_metrics(trace),
            diagnosis=trace.get("diagnosis", {}),
            run_id=trace.get("run_id"),
        )
        results.append(record)
        if not record["completed"]:
            break
    agent.close()
    return {
        "schema_version": 1,
        "scope": "read_only_conversation",
        "turns": results,
        "totals": totals(results),
        "completed": len(results) == len(QUESTIONS) * rounds
        and all(t["completed"] for t in results),
        "notice": (
            "Conclusão técnica não mede correção semântica. Revise as respostas. "
            "O modelo recebe código do projeto; diagnóstico local é atualizado."
        ),
    }


def totals(turns):
    fields = (
        "model_requests",
        "tool_calls",
        "repeated_calls",
        "compactions",
        "estimated_input_tokens",
        "reported_input_tokens",
        "reported_output_tokens",
    )
    return {
        "completed_turns": sum(bool(turn["completed"]) for turn in turns),
        "duration_ms": sum(turn["duration_ms"] for turn in turns),
        **{
            field: sum(values) if values else None
            for field in fields
            for values in [
                [
                    turn["metrics"][field]
                    for turn in turns
                    if isinstance(turn["metrics"].get(field), (int, float))
                ]
            ]
        },
        "notice": "Uso reportado pode cobrir parte das chamadas; consulte métricas por turno.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--rounds", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--baseline", type=Path, help="Relatório anterior com as mesmas perguntas")
    args = parser.parse_args()
    if not args.allow_model_calls:
        parser.error(
            "Informe --allow-model-calls: este benchmark envia dados ao provedor configurado."
        )
    report = benchmark(args.repo, create_provider(Settings.from_env()), args.rounds)
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if [turn["question"] for turn in baseline["turns"]] != [
            turn["question"] for turn in report["turns"]
        ]:
            parser.error("Baseline precisa ter o mesmo conjunto de turnos concluídos/tentados.")
        previous = totals(baseline["turns"])
        report["comparison"] = {
            "baseline": str(args.baseline),
            "delta": {
                key: value - previous[key]
                for key, value in report["totals"].items()
                if type(value) in (int, float) and type(previous.get(key)) in (int, float)
            },
            "notice": "Diferenças de custo/tempo não comprovam correção semântica.",
        }
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"Relatório: {args.output} · concluído: {report['completed']}")
    raise SystemExit(0 if report["completed"] else 1)


if __name__ == "__main__":
    main()

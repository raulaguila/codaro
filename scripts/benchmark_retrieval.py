"""Compare lexical and optional multilingual/code embeddings on one retrieval dataset.

Embedding weights can be downloaded only with --allow-downloads. No LLM calls.
Use the same dataset and machine for comparisons; cold-start time is recorded.
"""

import argparse
import json
import math
import time
from pathlib import Path

from codaro.evaluation import evaluate
from codaro.semantic import SemanticSearch
from codaro.trace import atomic_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evaluations/cases.json"))
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--allow-downloads", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.model and not args.allow_downloads:
        parser.error("Informe --allow-downloads para inicializar embeddings opcionais.")
    if args.model:
        try:
            from fastembed import TextEmbedding
        except ImportError:
            parser.error("Instale codaro[semantic] para comparar embeddings.")
        supported = {item["model"].lower() for item in TextEmbedding.list_supported_models()}
        unknown = [model for model in args.model if model.lower() not in supported]
        if unknown:
            parser.error("Modelos não suportados pelo fastembed instalado: " + ", ".join(unknown))
    reports = []
    for model in [None, *args.model]:
        started = time.monotonic()
        semantic = SemanticSearch(model=model) if model else None
        report = evaluate(args.cases, semantic=semantic)
        results = report["cases"]
        times = sorted(item["duration_ms"] for item in results)
        reports.append(
            {
                "model": model or "lexical",
                "semantic_status": semantic.status if semantic else "not_used",
                "duration_seconds": round(time.monotonic() - started, 3),
                "cases": len(results),
                "passed": report["passed"],
                "mean_recall": sum(item.get("expected_path_recall", 0) for item in results)
                / len(results),
                "mean_reciprocal_rank": sum(item.get("reciprocal_rank", 0) for item in results)
                / len(results),
                "p95_case_ms": times[max(0, math.ceil(len(times) * 0.95) - 1)],
                "retrieved_tokens_estimate": sum(
                    item.get("retrieved_tokens_estimate", 0) for item in results
                ),
                "results": results,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(
        args.output,
        json.dumps(
            {"schema_version": 1, "reports": reports}, ensure_ascii=False, indent=2
        ).encode(),
    )
    print(f"Relatório: {args.output}")
    raise SystemExit(
        1
        if any(
            report["passed"] != report["cases"]
            or report["semantic_status"].startswith("unavailable")
            for report in reports
        )
        else 0
    )


if __name__ == "__main__":
    main()

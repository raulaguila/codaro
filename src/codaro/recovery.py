"""Read-only reconciliation from durable pre-write checkpoints and task receipts."""

import json
from collections import Counter


def recovery_report(agent):
    edits = agent.edits.checkpoints.reconcile()
    task = agent.tasks.current()
    pending = Counter()
    commands = {}
    if task:
        for event in task.get("events", []):
            data = event
            if event["kind"] not in {"command_started", "command_finished"} or not data.get("argv"):
                continue
            key = json.dumps([data["argv"], data.get("cwd", ".")])
            commands[key] = {"argv": data["argv"], "cwd": data.get("cwd", ".")}
            if event["kind"] == "command_started":
                pending[key] += 1
            elif pending[key]:
                pending[key] -= 1
    return {
        "edits": edits,
        "commands": [
            {
                **commands[key],
                "unconfirmed_runs": count,
                "state": "unknown",
                "automatic_replay": False,
            }
            for key, count in pending.items()
            if count
        ],
        "review_required": bool(edits or any(pending.values())),
        "validation_inferred": False,
    }


def recovery_text(report):
    if not report["review_required"]:
        return "Nenhuma operação interrompida identificada."
    labels = {
        "matches_after": "conteúdo aplicado; validação pendente",
        "matches_before": "conteúdo anterior preservado",
        "conflict": "arquivo diverge dos snapshots; revise o diff",
        "unavailable": "arquivo indisponível ou bloqueado",
    }
    rows = ["Operações a revisar"]
    rows.extend(
        f"• {item['path']}: {labels[item['state']]} · {item['id']}" for item in report["edits"]
    )
    rows.extend(
        f"• Comando sem recibo de término: {item['argv'][0]} · diretório {item['cwd']}"
        for item in report["commands"]
    )
    rows.append(
        "Nenhuma ação foi repetida. Use /changes para revisar e autorize uma nova validação."
    )
    return "\n".join(rows)

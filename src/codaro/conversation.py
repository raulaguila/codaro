"""One conversation writer shared by the agent, CLI and UI.

The durable transcript is independent of the model's trimmed working history.
Decisions remain data from the user; they never create approval grants.
"""

import json
from copy import deepcopy
from hashlib import sha256
from threading import RLock

from codaro.sessions import validate_decisions


class Conversation:
    def __init__(self, store, *, ephemeral=False):
        self.store = store
        self.store.ephemeral = ephemeral
        self.completed = 0
        self._lock = RLock()

    def replace(self, turns, model, *, decisions=None, pending_decisions=None):
        with self._lock:
            self.store.save(turns, model, decisions=decisions, pending_decisions=pending_decisions)

    def complete(self, turn, model):
        with self._lock:
            self.replace(
                [*self.store.turns, deepcopy(turn)][-50:],
                model,
                decisions=[*self.store.decisions, self.store.pending_decisions][-50:],
                pending_decisions=[],
            )
            self.completed += 1

    def confirm(self, question, answer, model):
        decision = {"question": question, "answer": answer}
        validate_decisions([decision])
        with self._lock:
            pending = self.store.pending_decisions
            # The UI saves before releasing its waiting callback. CLI callbacks
            # arrive here directly, so both paths use the same durable decision.
            if pending and pending[-1] == decision:
                return
            if len(pending) >= 32:
                raise ValueError("Limite de decisões neste turno alcançado.")
            self.replace(self.store.turns, model, pending_decisions=[*pending, decision])

    def decisions(self):
        with self._lock:
            unique = {}
            for decision in [
                *(item for group in self.store.decisions for item in group),
                *self.store.pending_decisions,
            ]:
                encoded = json.dumps(decision, ensure_ascii=False, sort_keys=True)
                identifier = "decision:" + sha256(encoded.encode()).hexdigest()[:32]
                unique[identifier] = {**decision, "id": identifier}
            return deepcopy(list(unique.values()))

    def index_decisions(self, memory, model):
        decisions = self.decisions()
        if decisions:
            memory.record_decisions(decisions, model)

    def context(self, counter, budget):
        """Whole recent answers, plus retrieval pointers for those that do not fit."""
        selected, omitted = [], []
        notice = (
            "Decisões fora deste resumo: use search_conversation/read_conversation "
            "antes de alterar contratos."
        )

        def cost(text):
            return counter.count({"messages": [{"role": "user", "content": text}]})

        available = max(0, budget - cost(notice))
        used = 0
        for item in reversed(self.decisions()):
            text = json.dumps(item, ensure_ascii=False)
            size = cost(text)
            if used + size <= available:
                selected.append(text)
                used += size
            else:
                omitted.append(item["id"])
        if omitted:
            pointer = notice + " ID recente: " + omitted[0]
            if cost("\n".join([pointer, *reversed(selected)])) <= budget:
                notice = pointer
            selected.append(notice)
        return "\n".join(reversed(selected))

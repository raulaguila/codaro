"""Request-scoped controls shared by all model transports."""

import time
from contextvars import ContextVar

request_deadline = ContextVar("codaro_deadline", default=None)
request_artifacts = ContextVar("codaro_artifacts", default=None)
request_budget = ContextVar("codaro_budget", default=None)
request_redactor = ContextVar("codaro_redactor", default=None)


def remaining_seconds():
    deadline = request_deadline.get()
    return None if deadline is None else max(0.0, deadline() - time.monotonic())


def redact_request(value):
    redact = request_redactor.get()
    return redact(value) if redact else value


class RunBudget:
    """Shared across parent, summaries and exploration. Charges conservative estimates."""

    def __init__(self, max_requests=64, max_tokens=1_000_000):
        self.max_requests, self.max_tokens = max_requests, max_tokens
        self.requests = self.tokens = 0
        self.reserved_requests = self.reserved_tokens = 0

    def charge(self, input_tokens, output_reservation):
        from codaro.llm import ModelError

        cost = max(0, input_tokens) + max(0, output_reservation)
        if (
            self.requests >= self.max_requests - self.reserved_requests
            or self.tokens + cost > self.max_tokens - self.reserved_tokens
        ):
            raise ModelError("Orçamento global da atividade alcançado; progresso preservado.")
        self.requests += 1
        self.tokens += cost

    def near_limit(self, reservation):
        return (
            self.requests >= self.max_requests - self.reserved_requests - 1
            or self.tokens + 2 * reservation > self.max_tokens - self.reserved_tokens
        )

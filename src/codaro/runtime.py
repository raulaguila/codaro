"""Request-scoped controls shared by all model transports."""

import time
from contextvars import ContextVar

request_deadline = ContextVar("codaro_deadline", default=None)
request_redactor = ContextVar("codaro_redactor", default=None)


def remaining_seconds():
    deadline = request_deadline.get()
    return None if deadline is None else max(0.0, deadline() - time.monotonic())


def redact_request(value):
    redact = request_redactor.get()
    return redact(value) if redact else value

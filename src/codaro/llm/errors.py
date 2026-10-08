from __future__ import annotations

import json
import re


class ModelError(RuntimeError):
    """Provider failure with a user-facing message that excludes remote error bodies."""


class EmptyResponseError(ModelError):
    """Completed model response with neither usable text nor native tool calls."""


class OutputLimitError(ModelError):
    """Carry only plain text; incomplete tool arguments must never be executed."""

    def __init__(self, message, *, partial_text="", has_tool_calls=False):
        super().__init__(message)
        self.partial_text = (
            partial_text if isinstance(partial_text, str) and not has_tool_calls else ""
        )


class OllamaMemoryError(ModelError):
    """Requested native window exceeds available server memory; retry a smaller window."""


class ContextCapacityError(ModelError):
    """The irreducible turn cannot fit after automatic recovery."""


class ContextLimitError(ModelError):
    """Recognized context rejection; retry only the model, never executed tools."""

    def __init__(self, message, *, context_window=None):
        super().__init__(message)
        self.context_window = context_window


def reported_context_window(body):
    """Extract only explicit token limits, never the requested token count."""
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return None
    error = value.get("error") if isinstance(value, dict) else None
    if isinstance(error, dict):
        for field in ("max_context_length", "context_window", "context_length", "max_input_tokens"):
            limit = error.get(field)
            if type(limit) is int and 1024 <= limit <= 2_000_000:
                return limit
        error = error.get("message", "")
    if not isinstance(error, str):
        return None
    match = re.search(
        r"(?:maximum context length(?: is)?|context (?:window|length)(?: is| of)?|"
        r"maximum(?: number of)? (?:input )?tokens(?: is)?)\s*[:=]?\s*(\d[\d,]{0,12})\b",
        error,
        re.I,
    )
    if match:
        limit = int(match[1].replace(",", ""))
        if 1024 <= limit <= 2_000_000:
            return limit
    return None


def is_context_error(body: str) -> bool:
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return False
    error = value.get("error") if isinstance(value, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        if code in ("context_length_exceeded", "context_window_exceeded"):
            return True
        message = error.get("message", "")
    else:
        message = error if isinstance(error, str) else ""
    if not isinstance(message, str):
        return False
    message = message.lower()
    return any(
        phrase in message
        for phrase in (
            "maximum context length",
            "context length exceeded",
            "context window exceeded",
            "exceeds the context",
            "exceed the context",
            "exceeds context",
            "input length exceeds",
            "too many tokens",
            "prompt is too long",
            "too many input tokens",
            "context_length_exceeded",
        )
    )


class RequestCancelled(RuntimeError):
    pass

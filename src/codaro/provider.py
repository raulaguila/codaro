"""Compatibility imports. Model implementations live in codaro.llm."""

from codaro.llm import MAX_MESSAGE_CHARS as MAX_MESSAGE_CHARS
from codaro.llm import MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES
from codaro.llm import MAX_STREAM_BYTES as MAX_STREAM_BYTES
from codaro.llm import MAX_TOOL_ARGUMENT_BYTES as MAX_TOOL_ARGUMENT_BYTES
from codaro.llm import ContextCapacityError as ContextCapacityError
from codaro.llm import ContextLimitError as ContextLimitError
from codaro.llm import EmptyResponseError as EmptyResponseError
from codaro.llm import ModelError as ModelError
from codaro.llm import OllamaMemoryError as OllamaMemoryError
from codaro.llm import OpenAICompatible as OpenAICompatible
from codaro.llm import OutputLimitError as OutputLimitError
from codaro.llm import RequestCancelled as RequestCancelled
from codaro.llm import Settings as Settings
from codaro.llm import build_payload as build_payload
from codaro.llm import capture_wire as capture_wire
from codaro.llm import check_cancelled as check_cancelled
from codaro.llm import check_finish_reason as check_finish_reason
from codaro.llm import create_provider as create_provider
from codaro.llm import is_context_error as is_context_error
from codaro.llm import merge_fragment as merge_fragment
from codaro.llm import reported_context_window as reported_context_window
from codaro.llm import sse_events as sse_events
from codaro.llm import validate_message as validate_message

__all__ = [
    "Settings",
    "ModelError",
    "EmptyResponseError",
    "OutputLimitError",
    "OllamaMemoryError",
    "ContextCapacityError",
    "ContextLimitError",
    "reported_context_window",
    "is_context_error",
    "RequestCancelled",
    "MAX_RESPONSE_BYTES",
    "MAX_MESSAGE_CHARS",
    "MAX_TOOL_ARGUMENT_BYTES",
    "MAX_STREAM_BYTES",
    "build_payload",
    "capture_wire",
    "validate_message",
    "check_finish_reason",
    "check_cancelled",
    "merge_fragment",
    "sse_events",
    "OpenAICompatible",
    "create_provider",
]

"""Provider-independent API for model configuration and requests."""

from codaro.llm.config import Settings as Settings
from codaro.llm.errors import ContextCapacityError as ContextCapacityError
from codaro.llm.errors import ContextLimitError as ContextLimitError
from codaro.llm.errors import EmptyResponseError as EmptyResponseError
from codaro.llm.errors import ModelError as ModelError
from codaro.llm.errors import OllamaMemoryError as OllamaMemoryError
from codaro.llm.errors import OutputLimitError as OutputLimitError
from codaro.llm.errors import RequestCancelled as RequestCancelled
from codaro.llm.errors import is_context_error as is_context_error
from codaro.llm.errors import reported_context_window as reported_context_window
from codaro.llm.factory import create_provider as create_provider
from codaro.llm.openai import OpenAICompatible as OpenAICompatible
from codaro.llm.protocol import MAX_MESSAGE_CHARS as MAX_MESSAGE_CHARS
from codaro.llm.protocol import MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES
from codaro.llm.protocol import MAX_STREAM_BYTES as MAX_STREAM_BYTES
from codaro.llm.protocol import MAX_TOOL_ARGUMENT_BYTES as MAX_TOOL_ARGUMENT_BYTES
from codaro.llm.protocol import build_payload as build_payload
from codaro.llm.protocol import capture_wire as capture_wire
from codaro.llm.protocol import check_finish_reason as check_finish_reason
from codaro.llm.protocol import validate_message as validate_message
from codaro.llm.streaming import check_cancelled as check_cancelled
from codaro.llm.streaming import merge_fragment as merge_fragment
from codaro.llm.streaming import sse_events as sse_events

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

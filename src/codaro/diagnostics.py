"""Bounded measured diagnostics; no inferred server causes or stored credentials."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


@lru_cache(maxsize=1)
def source_fingerprint():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    try:
        for relative in (
            "trace.py",
            "diagnostics.py",
            "agent/controller.py",
            "agent/synthesis.py",
            "agent/prompts.py",
        ):
            digest.update(relative.encode())
            digest.update((root / relative).read_bytes())
    except OSError:
        return None
    return digest.hexdigest()


def breakdown(request):
    groups = {
        "instructions": [],
        "conversation": [],
        "tool_results": [],
        "tool_calls": [],
        "schemas": request.get("tools", []),
    }
    if request.get("system"):
        groups["instructions"].append(request["system"])
    for message in request.get("messages", []):
        category = (
            "instructions"
            if message.get("role") == "system"
            else "tool_results"
            if message.get("role") == "tool"
            else "tool_calls"
            if message.get("tool_calls")
            else "conversation"
        )
        groups[category].append(message)
    return {
        key: {"serialized_chars": len(encoded(value)), "utf8_bytes": len(encoded(value).encode())}
        for key, value in groups.items()
    }


def observe_stream(attempt, kind, value, elapsed_ms):
    stats = attempt.setdefault(
        "stream_summary",
        {
            "events": 0,
            "empty_deltas": 0,
            "content_chars": 0,
            "reasoning_chars": 0,
            "tool_call_fragments": 0,
            "unknown_fields": [],
        },
    )
    if kind not in {"sse", "ndjson", "response_body"}:
        return
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError, RecursionError):
        return
    if not isinstance(data, dict):
        return
    metadata = attempt.setdefault("server_metadata", {})
    for key in ("id", "model", "system_fingerprint"):
        if isinstance(data.get(key), str):
            metadata[key] = data[key][:200]
    stats["events"] += 1
    messages = [
        c.get("delta", c.get("message", {})) for c in data.get("choices", []) if isinstance(c, dict)
    ]
    if isinstance(data.get("message"), dict):
        messages.append(data["message"])
    block = data.get("content_block")
    if isinstance(block, dict):
        messages.append(
            {
                "content": block.get("text"),
                "reasoning": block.get("thinking"),
                "tool_calls": [block] if block.get("type") == "tool_use" else [],
            }
        )
    if isinstance(data.get("content"), list):
        for block in data["content"]:
            if isinstance(block, dict):
                messages.append(
                    {
                        "content": block.get("text"),
                        "reasoning": block.get("thinking"),
                        "tool_calls": [block] if block.get("type") == "tool_use" else [],
                    }
                )
    if isinstance(data.get("delta"), dict):
        delta = data["delta"]
        messages.append(
            {
                "content": delta.get("text"),
                "reasoning": delta.get("thinking"),
                "tool_calls": [delta] if delta.get("partial_json") else [],
            }
        )
    meaningful = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        stats["empty_deltas"] += not bool(message)
        content = message.get("content")
        reason = (
            message.get("reasoning_content") or message.get("reasoning") or message.get("thinking")
        )
        if isinstance(content, str):
            stats["content_chars"] += len(content)
            if content:
                meaningful = True
                stats.setdefault("first_content_ms", elapsed_ms)
        if isinstance(reason, str):
            stats["reasoning_chars"] += len(reason)
            if reason:
                meaningful = True
                stats.setdefault("first_reasoning_ms", elapsed_ms)
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            stats["tool_call_fragments"] += len(calls)
            meaningful = True
        for key in message:
            if (
                key
                not in {
                    "role",
                    "content",
                    "reasoning",
                    "reasoning_content",
                    "thinking",
                    "tool_calls",
                }
                and key not in stats["unknown_fields"]
                and len(stats["unknown_fields"]) < 20
            ):
                stats["unknown_fields"].append(key[:80])
    if meaningful:
        stats.setdefault("first_meaningful_fragment_ms", elapsed_ms)
        stats["last_meaningful_fragment_ms"] = elapsed_ms
    stats["classification"] = (
        "text"
        if stats["content_chars"]
        else "tool_calls"
        if stats["tool_call_fragments"]
        else "reasoning_only"
        if stats["reasoning_chars"]
        else "empty"
    )


def historical(path):
    return bool(re.search(r"(^|/)(audits?/|audit[^/]*\.)", path, re.I))


def implementation_path(path):
    from pathlib import PurePosixPath

    file = PurePosixPath(path)
    return (
        not historical(path)
        and not any(p in {"tests", "test", "fixtures"} for p in file.parts)
        and not file.name.startswith("test_")
        and file.suffix.lower()
        in {
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".go",
            ".rs",
            ".java",
            ".c",
            ".cpp",
            ".cs",
            ".rb",
            ".php",
            ".swift",
            ".kt",
            ".sh",
        }
    )

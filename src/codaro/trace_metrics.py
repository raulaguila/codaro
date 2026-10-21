"""Run totals independent of snapshot and archive retention."""

SETTINGS_FIELDS = (
    "timeout",
    "token_encoding",
    "context_window",
    "max_output_tokens",
    "api_style",
    "include_stream_usage",
    "context_source",
    "model",
)


def settings_snapshot(settings):
    return {name: getattr(settings, name, None) for name in SETTINGS_FIELDS}


def initial_totals():
    return {
        "model_requests": 0,
        "auxiliary_model_requests": 0,
        "http_attempts": 0,
        "tool_calls": 0,
        "repeated_calls": 0,
        "estimated_input_tokens": 0,
        "reported_input_tokens": 0,
        "reported_output_tokens": 0,
        "usage_requests_covered": 0,
        "output_usage_requests_covered": 0,
        "compactions": 0,
        "complete": True,
    }


def count_usage(totals, attempt, usage):
    if not isinstance(usage, dict):
        return
    previous = attempt.setdefault("usage_counted", {})
    for field, total, covered in (
        ("prompt_tokens", "reported_input_tokens", "usage_requests_covered"),
        ("completion_tokens", "reported_output_tokens", "output_usage_requests_covered"),
    ):
        value = usage.get(field)
        if type(value) is int and value >= 0:
            if field not in previous:
                totals[covered] += 1
            totals[total] += value - previous.get(field, 0)
            previous[field] = value


def evaluation_metrics(flow):
    """Legacy snapshots report their partial coverage rather than claiming totals."""
    totals = flow.get("totals")
    if isinstance(totals, dict):
        result = dict(totals)
    else:
        turns = flow.get("turns", [])
        seen = set()
        result = initial_totals()
        result["complete"] = not (
            flow.get("older_turns_in_archive") or flow.get("snapshot_limited")
        )
        result["model_requests"] = len(turns)
        for turn in turns:
            if "budget" not in turn:
                result["complete"] = False
            result["estimated_input_tokens"] += turn.get("budget", {}).get(
                "input_tokens_estimate", 0
            )
            result["tool_calls"] += len(turn.get("tool_results", []))
            for action in turn.get("tool_results", []):
                from codaro.diagnostics import fingerprint

                key = fingerprint(
                    [action.get("message", {}).get("name"), action.get("normalized_arguments")]
                )
                result["repeated_calls"] += key in seen
                seen.add(key)
            for attempt in turn.get("http_attempts", []):
                result["http_attempts"] += 1
                count_usage(result, {}, attempt.get("usage"))
        compactions = flow.get("compactions", [])
        result["compactions"] = len(compactions) if isinstance(compactions, list) else 0
    result["metrics_complete"] = result.pop("complete")
    if not result.get("usage_requests_covered"):
        result["reported_input_tokens"] = None
    if not result.get("output_usage_requests_covered"):
        result["reported_output_tokens"] = None
    return result

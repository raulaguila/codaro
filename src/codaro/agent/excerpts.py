"""Explicit, line-aware excerpts for text-only conclusions."""


def text_excerpt(text, limit):
    if len(text) <= limit:
        return text
    marker = "\n… [trecho omitido] …\n"
    if limit <= len(marker):
        return text[:limit]
    available = limit - len(marker)
    return text[: available // 2] + marker + text[-(available - available // 2) :]


def result_excerpt(original, limit=1200):
    result = dict(original)
    content = result.get("content")
    if isinstance(content, str) and len(content) > limit:
        prefix = content[:limit]
        result.update(content=prefix, truncated=True, synthesis_excerpt_only=True)
        start = result.get("start_line")
        if type(start) is int:
            lines = prefix.rstrip("\n").split("\n") if prefix else []
            end = start + len(lines) - 1
            source_lines = content.split("\n")
            partial = end if lines and lines[-1] != source_lines[len(lines) - 1] else None
            result.update(
                source_end_line=original.get("end_line"),
                end_line=end,
                partial_line=partial,
                next_start_line=partial if partial is not None else end + 1,
            )
    output = result.get("output")
    if isinstance(output, str) and len(output) > limit:
        result.update(
            output=text_excerpt(output, limit),
            truncated=True,
            synthesis_excerpt_only=True,
            source_output_chars=len(output),
        )
    return result

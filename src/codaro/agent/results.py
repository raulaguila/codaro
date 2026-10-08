from __future__ import annotations

from codaro.agent.messages import serialize


def fit_result(result: dict, budget: int) -> dict:
    if len(serialize(result)) <= budget:
        return result
    if "content" in result:
        result = dict(result)
        metadata = {
            key: result.get(key)
            for key in ("end_line", "partial_line", "next_start_line", "truncated")
        }
        content = result["content"]
        original = content.split("\n")
        old_partial = result.get("partial_line")

        def shorten(length):
            prefix = content[:length]
            result["content"] = prefix
            result.update(metadata)
            if length == len(content):
                return
            result["truncated"] = True
            lines = prefix.rstrip("\n").split("\n") if prefix else []
            end = result["start_line"] + len(lines) - 1
            result["end_line"] = end
            partial = (
                end
                if lines and lines[-1] != original[len(lines) - 1]
                else old_partial
                if old_partial is not None and old_partial <= end
                else None
            )
            result["partial_line"] = partial
            result["next_start_line"] = partial if partial is not None else end + 1

        low, high = 0, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            shorten(middle)
            if len(serialize(result)) <= budget:
                low = middle
            else:
                high = middle - 1
        shorten(low)
    elif "output" in result:
        result = dict(result)
        content = result["output"]
        result["output"] = ""
        result["truncated"] = True
        if len(serialize(result)) > budget:
            # The requested argv is already retained in tool_calls and the debug trace.
            result.pop("argv", None)
        low, high = 0, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            result["output"] = content[:middle]
            if len(serialize(result)) <= budget:
                low = middle
            else:
                high = middle - 1
        result["output"] = content[:low]
    elif "text" in result:
        result = dict(result)
        content = result["text"]
        low, high = 0, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            result["text"] = content[:middle]
            result["truncated"] = True
            result["next_offset"] = result.get("offset", 0) + middle
            if len(serialize(result)) <= budget:
                low = middle
            else:
                high = middle - 1
        result["text"] = content[:low]
        result["next_offset"] = result.get("offset", 0) + low
        if not low:
            return {"error": "Sem espaço para recuperar conversa; reduza o escopo."}
    elif "results" in result:
        result = dict(result)
        result["results"] = list(result["results"])
        result["truncated"] = True
        while result["results"] and len(serialize(result)) > budget:
            result["results"].pop()
    elif "tools" in result:
        result = dict(result)
        result["tools"] = list(result["tools"])
        result["truncated"] = True
        while result["tools"] and len(serialize(result)) > budget:
            result["tools"].pop()
            result["next_offset"] = result.get("offset", 0) + len(result["tools"])
        if not result["tools"]:
            return {"error": "Catálogo não cabe nesta página; solicite limit=1."}
    elif "files" in result:
        result = dict(result)
        result["files"] = list(result["files"])
        offset = (result.get("next_offset") or result["total"]) - len(result["files"])
        result["truncated"] = True
        while result["files"] and len(serialize(result)) > budget:
            result["files"].pop()
            result["next_offset"] = offset + len(result["files"])
        if not result["files"]:
            return {"error": "Sem espaço para listar caminhos. Reduza o escopo da pergunta."}
    if len(serialize(result)) > budget:
        if "artifact_id" in result:
            receipt = {
                "artifact_id": result["artifact_id"],
                "truncated": True,
                "notice": "Recupere páginas com read_artifact; saída histórica.",
            }
            if len(serialize(receipt)) <= budget:
                return receipt
        return {"error": "Resultado excede o orçamento. Solicite um intervalo menor."}
    return result

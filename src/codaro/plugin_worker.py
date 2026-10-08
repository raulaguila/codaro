"""Versioned, explicitly trusted Python plugin host. This is NOT an OS sandbox."""

import hashlib
import importlib.util
import json
import sys
import traceback


def main():
    from pathlib import Path

    from codaro.storage import private_read

    raw = private_read(Path(sys.argv[1]), 1_000_000)
    if hashlib.sha256(raw).hexdigest() != sys.argv[2]:
        raise ValueError("Plugin changed after trust validation")
    specification = importlib.util.spec_from_file_location("codaro_trusted_plugin", sys.argv[1])
    module = importlib.util.module_from_spec(specification)
    # Plugins may print; keep stdout exclusively for the wire protocol.
    wire = sys.stdout
    sys.stdout = sys.stderr
    # Execute precisely the bytes whose hash was accepted, not a second path read.
    exec(compile(raw, sys.argv[1], "exec"), module.__dict__)
    plugin = module.register()
    if not isinstance(plugin, dict) or plugin.get("api_version") != 1:
        raise ValueError("Plugin must declare api_version=1")
    tools = plugin.get("tools", [])
    commands = plugin.get("commands", [])
    if (
        not isinstance(tools, list)
        or not isinstance(commands, list)
        or len(tools) + len(commands) > 80
    ):
        raise ValueError("Invalid plugin catalog")
    by_name = {}
    for item in [*tools, *commands]:
        if not isinstance(item, dict) or not callable(item.get("handler")):
            raise ValueError("Plugin entries require a handler")
        if item["name"] in by_name:
            raise ValueError("Duplicate plugin entry")
        by_name[item["name"]] = item
    for line in sys.stdin.buffer:
        if len(line) > 1_000_000:
            break
        identifier = None
        try:
            request = json.loads(line)
            identifier = request.get("id")
            method, params = request["method"], request.get("params", {})
            if method == "initialize":
                result = {
                    "apiVersion": 1,
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "codaro-plugin"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": name,
                            "description": item.get("description", ""),
                            "inputSchema": item["parameters"],
                        }
                        for name, item in by_name.items()
                    ]
                }
            elif method == "tools/call":
                item = by_name[params["name"]]
                output = item["handler"](params["arguments"])
                result = {
                    "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]
                }
            elif method == "codaro/event":
                hook = plugin.get("on_event")
                if hook:
                    hook(params)
                continue
            elif method == "notifications/initialized":
                continue
            else:
                raise ValueError("Unsupported plugin method")
            response = {"jsonrpc": "2.0", "id": identifier, "result": result}
        except Exception:
            traceback.print_exc(file=sys.stderr)
            response = {
                "jsonrpc": "2.0",
                "id": identifier,
                "error": {"code": -32603, "message": "Plugin failed"},
            }
        if identifier is not None:
            raw = json.dumps(response, ensure_ascii=False)
            if len(raw.encode()) > 1_000_000:
                raw = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "error": {"code": -32603, "message": "Plugin output too large"},
                    }
                )
            wire.write(raw + "\n")
            wire.flush()


if __name__ == "__main__":
    main()

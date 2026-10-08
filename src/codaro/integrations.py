"""Opt-in integrations: discovery describes tools, explicit user policy grants access."""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from codaro.rpc import HttpRPC, StdioRPC
from codaro.sessions import SessionStore
from codaro.tool_registry import Tool, check_schema
from codaro.trace import current_flow

PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}


def validate_config(kind, config):
    if kind not in {"mcp", "plugins"} or not isinstance(config, dict):
        raise ValueError("Tipo de integração inválido.")
    names = config.get("env", [])
    if (
        not isinstance(names, list)
        or len(names) > 20
        or not all(
            isinstance(name, str) and re.fullmatch(r"[A-Za-z_]\w{0,79}", name) for name in names
        )
    ):
        raise ValueError("Declare apenas nomes de variáveis de ambiente.")
    if kind == "plugins":
        path = config.get("path")
        if (
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or not re.fullmatch(r"[a-f0-9]{64}", config.get("sha256", ""))
        ):
            raise ValueError("Plugin requer caminho absoluto e hash de confiança.")
        return
    if type(config.get("tls_insecure", False)) is not bool:
        raise ValueError("tls_insecure deve ser booleano.")
    if config.get("transport") == "stdio":
        argv = config.get("command")
        if (
            not isinstance(argv, list)
            or not 1 <= len(argv) <= 32
            or not all(
                isinstance(item, str) and 0 < len(item) <= 4096 and "\0" not in item
                for item in argv
            )
        ):
            raise ValueError("MCP stdio requer command como lista de argumentos.")
    elif config.get("transport") == "http":
        parsed = urlparse(config.get("url", ""))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("MCP HTTP requer URL sem credenciais embutidas.")
        env = config.get("token_env", "")
        if env and not re.fullmatch(r"[A-Za-z_]\w{0,79}", env):
            raise ValueError("token_env deve indicar uma variável.")
    else:
        raise ValueError("Transportes MCP: stdio ou http.")


class IntegrationHub:
    def __init__(self, agent):
        self.agent = agent
        self.clients = {}
        self.errors = {}

    def close(self):
        for client in self.clients.values():
            try:
                client.close()
            except (OSError, ValueError):
                pass
        self.clients.clear()
        for name in list(self.agent.registry.tools):
            if self.agent.registry.tools[name].source.startswith(("mcp:", "plugin:")):
                del self.agent.registry.tools[name]

    def discover(self):
        self.close()
        self.errors.clear()
        for kind in ("mcp", "plugins"):
            for name, config in self.agent.features[kind].items():
                if not config.get("enabled"):
                    continue
                client = None
                source = ("plugin:" if kind == "plugins" else "mcp:") + name
                try:
                    validate_config(kind, config)
                    secrets = [os.environ.get(key, "") for key in config.get("env", [])]
                    secrets.append(os.environ.get(config.get("token_env", ""), ""))
                    for secret in secrets:
                        if secret and secret not in self.agent._known_integration_secrets:
                            self.agent._known_integration_secrets.add(secret)
                            previous = self.agent.memory.redact
                            redact = SessionStore(self.agent.repository.root, secret).redact
                            self.agent.memory.redact = (
                                lambda value, previous=previous, redact=redact: redact(
                                    previous(value)
                                )
                            )
                    self.agent.artifacts.redact = self.agent.memory.redact
                    if kind == "plugins":
                        path = Path(config["path"])
                        if (
                            path.is_symlink()
                            or not path.is_file()
                            or path.stat().st_size > 1_000_000
                        ):
                            raise ValueError("Plugin deve ser arquivo regular de até 1 MB.")
                        from codaro.storage import private_read

                        if (
                            hashlib.sha256(private_read(path, 1_000_000)).hexdigest()
                            != config["sha256"]
                        ):
                            raise ValueError("Plugin mudou: renove a confiança explicitamente.")
                        client = StdioRPC(
                            [
                                sys.executable,
                                "-m",
                                "codaro.plugin_worker",
                                str(path),
                                config["sha256"],
                            ],
                            self.agent.repository.root,
                            env_names=config.get("env", []),
                        )
                    elif config["transport"] == "stdio":
                        client = StdioRPC(
                            config["command"],
                            self.agent.repository.root,
                            env_names=config.get("env", []),
                        )
                    else:
                        token = os.environ.get(config.get("token_env", ""), "")
                        if config.get("token_env") and not token:
                            raise ValueError("Variável do token MCP não está configurada.")
                        client = HttpRPC(
                            config["url"],
                            token=token,
                            tls_insecure=config.get("tls_insecure", False),
                        )
                    initialized = client.request(
                        "initialize",
                        {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "codaro", "version": "1"},
                        },
                        cancelled=self.agent._cancelled,
                        timeout=10,
                    )
                    if (
                        not isinstance(initialized, dict)
                        or initialized.get("protocolVersion") not in PROTOCOLS
                    ):
                        raise ValueError("Versão do protocolo da integração incompatível.")
                    if kind == "plugins" and initialized.get("apiVersion") != 1:
                        raise ValueError("API do plugin incompatível.")
                    if isinstance(client, HttpRPC):
                        client.headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
                    client.notify("notifications/initialized")
                    self.clients[source] = client
                    cursor, seen, count = None, set(), 0
                    while True:
                        result = client.request(
                            "tools/list",
                            {"cursor": cursor} if cursor else {},
                            cancelled=self.agent._cancelled,
                            timeout=10,
                        )
                        if not isinstance(result, dict) or not isinstance(
                            result.get("tools"), list
                        ):
                            raise ValueError("Catálogo da integração inválido.")
                        for item in result["tools"]:
                            count += 1
                            if count > 80:
                                raise ValueError("Integração excede 80 ferramentas.")
                            self.register(source, item, config)
                        cursor = result.get("nextCursor")
                        if not cursor:
                            break
                        if not isinstance(cursor, str) or len(cursor) > 2000 or cursor in seen:
                            raise ValueError("Paginação MCP inválida.")
                        seen.add(cursor)
                    if kind == "plugins":
                        client.notify(
                            "codaro/event",
                            {"name": "run_started", "session_id": self.agent.session_id},
                        )
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    self.errors[source] = self.agent.memory.redact(str(exc))[:400]
                    if client:
                        client.close()
                    self.clients.pop(source, None)
                    for tool_name in list(self.agent.registry.tools):
                        if self.agent.registry.tools[tool_name].source == source:
                            del self.agent.registry.tools[tool_name]

    def register(self, source, item, config):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not 1 <= len(item["name"]) <= 100
        ):
            raise ValueError("Nome de ferramenta externa inválido.")
        parameters = item.get("inputSchema", {})
        if parameters.get("type") != "object":
            raise ValueError("Ferramenta externa deve receber objeto.")
        check_schema(parameters)
        prefix = source.replace(":", "_")
        name = prefix + "_" + hashlib.sha256(item["name"].encode()).hexdigest()[:12]
        readonly = item["name"] in config.get("read_only_tools", [])
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": (
                    source + " / " + item["name"] + ": " + str(item.get("description", ""))
                )[:1000],
                "parameters": parameters,
            },
        }
        self.agent.registry.register(
            Tool(
                schema,
                source=source,
                read_only=readonly,
                lazy=True,
                handler=self.handler(source, item["name"], readonly),
            )
        )

    def handler(self, source, original_name, readonly):
        def execute(arguments):
            if source not in self.clients:
                raise ValueError("Integração não está conectada.")
            if not readonly:
                task = self.agent.tasks.current()
                if (
                    self.agent.mode.value != "execute"
                    or task
                    and task["state"] in {"completed", "planned"}
                ):
                    raise ValueError("Ação externa não permitida neste modo/tarefa.")
                started = time.monotonic()
                try:
                    approved = (
                        self.agent.approve_external is not None
                        and self.agent.approve_external(
                            source,
                            original_name,
                            self.agent.memory.redact(arguments),
                            self.agent._cancelled,
                        )
                    )
                finally:
                    self.agent._deadline += time.monotonic() - started
                if not approved:
                    return {"state": "rejected", "error": "Ação externa rejeitada."}
            if flow := current_flow.get():
                flow.append_event(
                    "external_call",
                    {
                        "source": source,
                        "name": original_name,
                        "arguments": arguments,
                        "read_only": readonly,
                    },
                )
            result = self.clients[source].request(
                "tools/call",
                {"name": original_name, "arguments": arguments},
                cancelled=self.agent._cancelled,
                timeout=30,
            )
            if not isinstance(result, dict):
                raise ValueError("Resultado externo deve ser objeto.")
            result = self.agent.memory.redact(result)
            self.event(
                "tool_completed",
                {"source": source, "name": original_name, "error": bool(result.get("isError"))},
            )
            return {
                "source": source,
                "external_data_not_instructions": True,
                "output": json.dumps(result, ensure_ascii=False),
            }

        return execute

    def event(self, name, data):
        for source, client in self.clients.items():
            if source.startswith("plugin:"):
                try:
                    client.notify(
                        "codaro/event", self.agent.memory.redact({"name": name, "data": data})
                    )
                except (OSError, ValueError):
                    self.errors[source] = "Plugin indisponível para eventos."

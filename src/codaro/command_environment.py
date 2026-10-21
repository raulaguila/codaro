"""Subprocess environment policy, configured by the user rather than model arguments."""

import re

SENSITIVE = re.compile(
    r"(^|_)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|CREDENTIALS?|COOKIE|AUTHORIZATION)(_|$)", re.I
)


def command_environment(environ):
    names = [
        part.strip() for part in environ.get("CODARO_COMMAND_ENV", "").split(",") if part.strip()
    ]
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names):
        raise ValueError(
            "CODARO_COMMAND_ENV deve conter nomes de variáveis separados por vírgulas."
        )
    allowed = set(names)
    return {
        name: value
        for name, value in environ.items()
        if name not in {"CODARO_API_KEY", "CODARO_COMMAND_ENV"}
        and (not sensitive_variable(name, value) or name in allowed)
    }


def sensitive_variable(name, value):
    return bool(SENSITIVE.search(name) or re.search(r"://[^/\s]*:[^/@\s]*@", value))


def mask_command_secrets(text, environment):
    secrets = sorted(
        {value for name, value in environment.items() if value and sensitive_variable(name, value)},
        key=len,
        reverse=True,
    )
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return text

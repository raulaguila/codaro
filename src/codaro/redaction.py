"""Conservative credential masking shared by persistence and model requests.

Known credentials are still matched exactly by SessionStore. These additional
patterns catch recognizable credentials in repository content, not arbitrary
high-entropy strings, hashes, identifiers or all possible secrets.
"""

import re

_TOKEN = re.compile(
    r"(?<![\w-])(?:"
    r"sk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{32,}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{40,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|AIza[A-Za-z0-9_-]{35}"
    r")(?![\w-])"
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]{20,}=*")
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:[A-Z0-9]{1,40}_){0,6}(?:API_KEY|API_TOKEN|ACCESS_TOKEN|AUTH_TOKEN|"
    r"SECRET_KEY|CLIENT_SECRET|PASSWORD|AWS_SECRET_ACCESS_KEY)"
    r"[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9_./+~=-]{12,})"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN ((?:RSA |EC |OPENSSH )?PRIVATE KEY)-----"
    r"[\s\S]{1,32000}?-----END \1-----"
)
_FIELD = re.compile(
    r"(?i)^(?:[A-Z0-9]+_)*(?:API_KEY|API_TOKEN|ACCESS_TOKEN|AUTH_TOKEN|"
    r"SECRET_KEY|CLIENT_SECRET|PASSWORD|AWS_SECRET_ACCESS_KEY|AUTHORIZATION)$"
)
_PLACEHOLDERS = {"", "[REDACTED]", "redacted", "example", "changeme", "your_api_key"}


def redact_credentials(value):
    if isinstance(value, str):
        value = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
        value = _TOKEN.sub("[REDACTED]", value)
        value = _BEARER.sub(r"\1[REDACTED]", value)
        return _ASSIGNMENT.sub(r"\1[REDACTED]", value)
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    if isinstance(value, dict):
        return {
            redact_credentials(key): (
                "[REDACTED]"
                if isinstance(key, str)
                and _FIELD.fullmatch(key)
                and isinstance(item, str)
                and item not in _PLACEHOLDERS
                else redact_credentials(item)
            )
            for key, item in value.items()
        }
    return value

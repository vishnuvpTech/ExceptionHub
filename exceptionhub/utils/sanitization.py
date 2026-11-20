"""Sensitive-data filtering for event payloads.

The SDK must never forward passwords, tokens or secret headers to the
webhook. Values for any configured sensitive field name are replaced with
``[FILTERED]``.
"""

from __future__ import annotations

import re
from typing import Any

#: Default sensitive field names (lower-cased, underscore-normalised).
DEFAULT_SENSITIVE_FIELDS = (
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credentials",
    "private_key",
    "webhook_key",
)

#: Matches common key material wherever it appears in a string value.
_KEY_MATERIAL_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret|token|password|passwd|authorization|cookie)\b\s*[:=]\s*)([^\s,;'\"}\]]{4,})"
)


def _normalize(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


class SensitiveDataSanitizer:
    """Recursively redacts sensitive values in event payloads."""

    def __init__(self, sensitive_fields: tuple[str, ...] = DEFAULT_SENSITIVE_FIELDS) -> None:
        self._fields = {_normalize(f) for f in sensitive_fields}

    def sanitize(self, data: Any) -> Any:
        """Return *data* with sensitive values replaced, new structures only."""
        return self._walk(data)

    def _walk(self, data: Any) -> Any:
        if isinstance(data, dict):
            out = {}
            for key, value in data.items():
                if _normalize(key) in self._fields:
                    out[key] = "[FILTERED]"
                else:
                    out[key] = self._walk(value)
            return out
        if isinstance(data, (list, tuple)):
            return [self._walk(item) for item in data]
        if isinstance(data, str):
            return self._redact_string(data)
        return data

    @staticmethod
    def _redact_string(value: str) -> str:
        """Redact embedded key/value pairs such as ``Authorization: Bearer ***``."""
        if "Bearer " in value or "Authorization" in value:
            # Strip raw bearer tokens from strings that happen to carry them.
            value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}", r"\1[FILTERED]", value)
        return _KEY_MATERIAL_RE.sub(lambda m: m.group(1) + "[FILTERED]", value)

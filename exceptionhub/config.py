"""Configuration for the ExceptionHub monitoring SDK.

Configuration values are resolved in the following priority order:
1. Explicit keyword arguments passed to :class:`MonitorConfig`.
2. Environment variables (``EXCEPTIONHUB_*``).
3. Built-in defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# Environment variable prefix used by the SDK.
_ENV_PREFIX = "EXCEPTIONHUB_"

#: Fields that environment variables can supply.
_ENV_FIELDS = {
    "project_name": "PROJECT_NAME",
    "webhook_url": "WEBHOOK_URL",
    "webhook_key": "WEBHOOK_KEY",
    "environment": "ENVIRONMENT",
    "release": "RELEASE",
}

_ENV_BOOL = {"capture_exceptions", "capture_performance", "debug", "enabled"}
_ENV_FLOAT = {"performance_threshold"}
_ENV_INT = {"timeout", "max_queue_size", "max_retries", "sample_rate"}


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(_ENV_PREFIX + name.upper())
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str) -> float | None:
    raw = os.environ.get(_ENV_PREFIX + name.upper())
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _env_int(name: str) -> int | None:
    raw = os.environ.get(_ENV_PREFIX + name.upper())
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class MonitorConfig:
    """Validated configuration for a :class:`~exceptionhub.client.Monitor` instance.

    Any argument that is left as ``None`` falls back to an environment
    variable and then to a default.
    """

    project_name: str | None = None
    webhook_url: str | None = None
    webhook_key: str | None = None
    environment: str = "development"
    release: str | None = None
    capture_exceptions: bool = True
    capture_performance: bool = True
    performance_threshold: float = 2.0
    timeout: float = 5.0
    debug: bool = False
    enabled: bool = True
    max_queue_size: int = 1000
    max_retries: int = 3
    sample_rate: float = 1.0
    sensitive_fields: tuple[str, ...] = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credentials",
        "passwd",
        "private_key",
    )
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._apply_environment()
        self._apply_defaults()
        self._validate()

    # -- resolution helpers -------------------------------------------------

    def _apply_environment(self) -> None:
        """Fill any missing value from ``EXCEPTIONHUB_*`` environment variables."""
        for attr, env in _ENV_FIELDS.items():
            if getattr(self, attr) is None:
                value = os.environ.get(_ENV_PREFIX + env)
                if value:
                    setattr(self, attr, value)
        for attr in _ENV_BOOL:
            if os.environ.get(_ENV_PREFIX + attr.upper()) is not None:
                setattr(self, attr, _env_bool(attr))
        for attr in _ENV_FLOAT:
            if os.environ.get(_ENV_PREFIX + attr.upper()) is not None:
                setattr(self, attr, _env_float(attr))
        for attr in _ENV_INT:
            if os.environ.get(_ENV_PREFIX + attr.upper()) is not None:
                setattr(self, attr, _env_int(attr))

    def _apply_defaults(self) -> None:
        if self.project_name is None:
            self.project_name = os.environ.get("APP_NAME", "unknown-project")
        if self.webhook_url is None:
            self.webhook_url = "http://localhost:8000/api/events/"
        if self.webhook_key is None:
            self.webhook_key = "insecure-default-key"
        if self.release is None:
            self.release = "0.0.0"

    def _validate(self) -> None:
        if not self.webhook_url.startswith(("http://", "https://")):
            raise ValueError(f"webhook_url must start with http:// or https://: {self.webhook_url!r}")
        if not self.project_name:
            raise ValueError("project_name must be a non-empty string")
        if self.timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        if self.performance_threshold < 0:
            raise ValueError("performance_threshold must be >= 0")
        if self.max_queue_size <= 0:
            raise ValueError("max_queue_size must be a positive integer")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if not 0.0 <= self.sample_rate <= 1.0:
            raise ValueError("sample_rate must be between 0.0 and 1.0")

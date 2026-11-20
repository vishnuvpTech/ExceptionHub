"""Message and custom event capture."""

from __future__ import annotations

from typing import Any

from exceptionhub.config import MonitorConfig
from exceptionhub.context import get_context
from exceptionhub.schemas.custom import CustomEvent, MessageEvent
from exceptionhub.utils.fingerprint import message_fingerprint
from exceptionhub.utils.sanitization import SensitiveDataSanitizer
from exceptionhub.utils.serializer import serialize_event
from exceptionhub.utils.system import get_system_info


def build_message_event(
    config: MonitorConfig,
    message: str,
    *,
    level: str = "info",
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a serialized message event for manual reporting."""
    event = MessageEvent(
        project={
            "name": config.project_name,
            "environment": config.environment,
            "release": config.release,
        },
        severity=level,  # type: ignore[arg-type]
        message=message,
        system=get_system_info(),
        fingerprint=message_fingerprint(config.project_name, message),
    )
    merged = get_context().to_event_fields()
    if tags:
        merged["tags"] = {**merged.get("tags", {}), **tags}
    if extra:
        merged["extra"] = {**merged.get("extra", {}), **extra}
    event = event.model_copy(update=merged)
    return SensitiveDataSanitizer(config.sensitive_fields).sanitize(serialize_event(event))


def build_custom_event(
    config: MonitorConfig,
    category: str,
    payload: dict[str, Any] | None = None,
    *,
    level: str = "info",
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Build a serialized custom application event.

    *category* identifies the kind of custom event ("db_query", "api_call",
    "memory", "cpu", ...) and keeps the receiver able to route new kinds as
    the SDK grows.
    """
    event = CustomEvent(
        project={
            "name": config.project_name,
            "environment": config.environment,
            "release": config.release,
        },
        severity=level,  # type: ignore[arg-type]
        category=category,
        payload=payload or {},
        system=get_system_info(),
        extra=extra or {},
    )
    if event_id:
        event.event_id = event_id  # supports duplicate suppression by caller
    merged = get_context().to_event_fields()
    if tags:
        merged["tags"] = {**merged.get("tags", {}), **tags}
    if extra:
        merged["extra"] = {**merged.get("extra", {}), **extra}
    event = event.model_copy(update=merged)
    return SensitiveDataSanitizer(config.sensitive_fields).sanitize(serialize_event(event))

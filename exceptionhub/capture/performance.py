"""Performance capture: timing events for slow operations."""

from __future__ import annotations

from typing import Any

from exceptionhub.config import MonitorConfig
from exceptionhub.context import get_context
from exceptionhub.schemas.performance import PerformanceDetail, PerformanceEvent
from exceptionhub.utils.fingerprint import performance_fingerprint
from exceptionhub.utils.sanitization import SensitiveDataSanitizer
from exceptionhub.utils.serializer import serialize_event
from exceptionhub.utils.system import get_system_info


def build_performance_event(
    config: MonitorConfig,
    operation: str,
    execution_time: float,
    threshold: float | None = None,
    *,
    report_all: bool = False,
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a performance event; returns ``None`` when it should not be reported.

    An event is produced when the operation was slow (``execution_time``
    meets or exceeds *threshold*) or when *report_all* is set, which is
    useful in development.
    """
    effective_threshold = config.performance_threshold if threshold is None else threshold
    is_slow = execution_time >= effective_threshold
    if not (is_slow or report_all):
        return None

    detail = PerformanceDetail(
        operation=operation,
        execution_time=round(float(execution_time), 6),
        threshold=effective_threshold,
        status="slow" if is_slow else "ok",
    )
    event = PerformanceEvent(
        project={
            "name": config.project_name,
            "environment": config.environment,
            "release": config.release,
        },
        severity="warning" if is_slow else "info",
        performance=detail,
        system=get_system_info(),
        fingerprint=performance_fingerprint(config.project_name, operation),
    )
    merged = get_context().to_event_fields()
    if tags:
        merged["tags"] = {**merged.get("tags", {}), **tags}
    if extra:
        merged["extra"] = {**merged.get("extra", {}), **extra}
    event = event.model_copy(update=merged)
    return SensitiveDataSanitizer(config.sensitive_fields).sanitize(serialize_event(event))

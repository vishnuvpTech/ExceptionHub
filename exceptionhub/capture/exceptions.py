"""Exception capture: event building and the unhandled-exception hook.

The unhandled hook wraps ``sys.excepthook`` and always forwards to the
original handler first, so the application's normal behaviour (traceback
printout, crash) is completely unchanged.
"""

from __future__ import annotations

import sys
from typing import Any

from exceptionhub.config import MonitorConfig
from exceptionhub.context import get_context
from exceptionhub.schemas.exception import ExceptionDetail, ExceptionEvent
from exceptionhub.utils.fingerprint import exception_fingerprint
from exceptionhub.utils.sanitization import SensitiveDataSanitizer
from exceptionhub.utils.serializer import serialize_event
from exceptionhub.utils.system import get_system_info


def current_exception() -> BaseException | None:
    """Return the exception active in the current frame, if any."""
    return sys.exc_info()[1]


def build_exception_event(
    config: MonitorConfig,
    exc: BaseException | None = None,
    *,
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    severity: str = "error",
) -> dict[str, Any]:
    """Build a fully populated, serialized exception event.

    When *exc* is ``None`` the exception active in the current frame
    (``sys.exc_info()``) is used; if there is none, a marker event of type
    ``UnknownException`` is emitted so the call still records *something*.
    """
    if exc is None:
        exc = current_exception()
    if exc is None:
        detail = ExceptionDetail(type="UnknownException", message="No active exception", stack_trace="")
    else:
        detail = ExceptionDetail.from_exception(exc)

    event = ExceptionEvent(
        project={
            "name": config.project_name,
            "environment": config.environment,
            "release": config.release,
        },
        severity=severity,
        exception=detail,
        system=get_system_info(),
        fingerprint=exception_fingerprint(config.project_name, detail.type, detail.function, detail.line_number),
    )
    merged = get_context().to_event_fields()
    if tags:
        merged["tags"] = {**merged.get("tags", {}), **tags}
    if extra:
        merged["extra"] = {**merged.get("extra", {}), **extra}
    event = event.model_copy(update=merged)
    return SensitiveDataSanitizer(config.sensitive_fields).sanitize(serialize_event(event))


class UnhandledExceptionHandler:
    """Chains onto the original ``sys.excepthook`` and reports exceptions.

    Instantiating one and assigning it to ``sys.excepthook`` is what wires
    up automatic capture; :meth:`restore` puts the interpreter back the way
    it was.
    """

    def __init__(self, capture, original: Any) -> None:
        self._capture = capture  # callable(exc_value, exc_tb) provided by Monitor
        self._original = original

    def __call__(self, exc_type: type, exc_value: BaseException, exc_tb: Any) -> None:
        # Preserve the interpreter's normal behaviour first.
        try:
            if self._original is not None:
                self._original(exc_type, exc_value, exc_tb)
        except Exception:  # noqa: BLE001
            pass
        # Then report - a monitoring failure must never affect the app.
        try:
            self._capture(exc_value, exc_tb)
        except Exception:  # noqa: BLE001
            pass

    def restore(self) -> None:
        if self._original is not None:
            sys.excepthook = self._original
        else:
            sys.excepthook = _default_excepthook()
        self._original = None


def _default_excepthook() -> Any:
    import sys

    class _Default:
        def __call__(self, et, ev, tb):
            sys.__excepthook__(et, ev, tb)

    return _Default()


def thread_unhandled_hook(capture) -> Any:
    """A ``threading.excepthook`` replacement that reports and defers.

    Returns the replacement hook; ``threading.excepthook`` still runs the
    original logic so thread crash diagnostics are unchanged.
    """

    def hook(args: dict[str, Any]) -> None:
        exc = args.get("exc_value")
        if exc is not None:
            try:
                capture(exc, None)
            except Exception:  # noqa: BLE001
                pass

    return hook

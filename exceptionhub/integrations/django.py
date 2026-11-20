"""Django integration: middleware + unhandled-request signals.

Two pieces work together:

1. App registration (add to ``INSTALLED_APPS``)::

       INSTALLED_APPS = [
           ...
           "exceptionhub.integrations.django",
       ]

   The app's ``ready()`` hook connects Django's ``request_unhandled``
   signal so exceptions that escape a request are captured automatically.

2. Optional middleware (add to ``MIDDLEWARE``)::

       MIDDLEWARE = [
           ...
           "exceptionhub.integrations.django.ExceptionHubMiddleware",
       ]

   The middleware times each request, attaches a correlation id and the
   HTTP context to every event, and reports slow requests.

Configure either with ``register_monitor(monitor)`` or by adding to
``settings``::

       EXCEPTIONHUB = {
           "project_name": "django-demo",
           "webhook_url": "https://monitor.example.com/api/events/",
           "webhook_key": "PROJECT_WEBHOOK_SECRET",
       }
"""

from __future__ import annotations

import time
from typing import Any

from exceptionhub.client import Monitor
from exceptionhub.utils.ids import new_request_id

_registered_monitor: Monitor | None = None


def register_monitor(monitor: Monitor) -> Monitor:
    """Register the Monitor to use for this Django process."""
    global _registered_monitor
    _registered_monitor = monitor
    if not monitor._initialized:  # noqa: SLF001
        monitor.initialize()
    return monitor


def get_monitor() -> Monitor:
    """Return the registered monitor, creating one from settings if needed."""
    global _registered_monitor
    if _registered_monitor is None:
        from django.conf import settings

        cfg = dict(getattr(settings, "EXCEPTIONHUB", None) or {})
        _registered_monitor = Monitor(**cfg)
    if not _registered_monitor._initialized:  # noqa: SLF001
        _registered_monitor.initialize()
    return _registered_monitor


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class ExceptionHubMiddleware:
    """Class-based middleware (``MIDDLEWARE`` setting) for request monitoring.

    Supports: unhandled request exceptions, API execution time, HTTP method
    and endpoint, response status code, request correlation id, slow
    request detection.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        monitor = get_monitor()
        request_id = getattr(request, "headers", {}).get("X-Request-Id") or new_request_id()
        request._exceptionhub_request_id = request_id  # type: ignore[attr-defined]
        request._exceptionhub_start = time.perf_counter()  # type: ignore[attr-defined]

        monitor.start_request_context(
            request={
                "method": request.method,
                "url": request.path,
                "request_id": request_id,
            },
            request_id=request_id,
        )
        response = self.get_response(request)

        if getattr(request, "_exceptionhub_start", None) is not None:
            elapsed = time.perf_counter() - request._exceptionhub_start
            response.headers["X-Request-Id"] = request_id
            _report_slow_request(monitor, request, response.status_code, elapsed, request_id)
        return response

    def process_exception(self, request: Any, exception: Exception) -> None:
        """Capture the exception; returning None keeps Django's own handling."""
        try:
            monitor = get_monitor()
            monitor.capture_exception(
                exception,
                tags={"framework": "django", "method": request.method, "url": request.path},
            )
        except Exception:  # noqa: BLE001 - never affect request handling
            pass
        return None


def _report_slow_request(monitor: Monitor, request: Any, status_code: int, elapsed: float, request_id: str) -> None:
    from exceptionhub.capture.performance import build_performance_event

    threshold = monitor._config.performance_threshold  # noqa: SLF001
    if elapsed < threshold:
        return
    event = build_performance_event(
        monitor._config,
        f"http {request.method} {request.path}",
        elapsed,
        threshold=threshold,
        tags={"framework": "django"},
        extra={"status_code": status_code, "request_id": request_id},
    )
    if event is not None:
        event["request"] = {
            "method": request.method,
            "url": request.path,
            "status_code": status_code,
            "request_id": request_id,
        }
        monitor.capture_event(event, dedupe=False)


# ---------------------------------------------------------------------------
# Signals: request exceptions
# ---------------------------------------------------------------------------


def _on_request_exception(sender: Any, request: Any = None, response: Any = None, **kwargs: Any) -> None:
    """Handle Django's ``got_request_exception`` signal.

    Django emits this signal when an uncaught exception escapes a request;
    the signal carries the *request* and the *response* but not the
    exception object itself, so we read it from the active ``sys.exc_info``
    at signal-delivery time (which the exception handler keeps live).
    """
    import sys

    try:
        monitor = get_monitor()
        exc_info = sys.exc_info()
        exception: Any = kwargs.get("exception") or exc_info[1]
        if exception is None:
            return
        tags: dict[str, Any] = {"framework": "django"}
        extra: dict[str, Any] = {"unhandled": True}
        if request is not None:
            tags.update(method=getattr(request, "method", None), url=getattr(request, "path", None))
        if response is not None:
            extra["response_status"] = getattr(response, "status_code", None)
        monitor.capture_exception(exception, severity="critical", tags=tags, extra=extra)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# App registration
# ---------------------------------------------------------------------------


class ExceptionHubDjangoConfig:
    """Minimal app config so the module can appear in ``INSTALLED_APPS``.

    Uses a plain class (no ``django.apps`` import at module import time) so
    the SDK can be installed without Django being present; the real wiring
    happens when Django actually loads the app.
    """

    name = "exceptionhub.integrations.django"
    label = "exceptionhub_django"

    def ready(self) -> None:
        from django.core import signals

        # Django emits got_request_exception for uncaught request errors;
        # very old releases used request_unhandled. Connect to whichever
        # is present.
        if hasattr(signals, "got_request_exception"):
            signals.got_request_exception.connect(
                _on_request_exception, dispatch_uid="exceptionhub.got_request_exception"
            )
        elif hasattr(signals, "request_unhandled"):  # pragma: no cover
            signals.request_unhandled.connect(_on_request_exception, dispatch_uid="exceptionhub.request_unhandled")


# Django's app loader looks for an AppConfig subclass; provide a compatible
# one only when Django is importable, so core-SDK consumers never need it.
try:
    from django.apps import AppConfig as _DjangoAppConfig

    class _ConcreteConfig(_DjangoAppConfig, ExceptionHubDjangoConfig):
        pass

    ExceptionHubDjangoConfig = _ConcreteConfig  # noqa: F811
except ImportError:  # pragma: no cover - core install without Django
    pass

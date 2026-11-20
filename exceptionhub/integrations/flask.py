"""Flask integration: error handler + slow-request hook.

Usage::

    from flask import Flask
    from exceptionhub import Monitor
    from exceptionhub.integrations.flask import instrument_flask

    monitor = Monitor(project_name="flask-demo", webhook_url=...)
    monitor.initialize()
    app = Flask(__name__)
    instrument_flask(app, monitor)

``instrument_flask`` registers:
- an error handler that captures unhandled exceptions (Flask still renders
  its normal error response),
- a ``before_request``/``after_request`` pair that times the request,
  attaches a correlation id and reports slow requests,
- teardown hooks so the context never leaks between requests.
"""

from __future__ import annotations

import time
from typing import Any

from exceptionhub.client import Monitor
from exceptionhub.utils.ids import new_request_id


def instrument_flask(app: Any, monitor: Monitor) -> Monitor:
    """Attach ExceptionHub to a Flask application. Returns the monitor."""
    if not monitor._initialized:  # noqa: SLF001
        monitor.initialize()

    @app.before_request
    def _sl_start() -> None:
        from flask import g, request

        request_id = getattr(request.headers, "X-Request-Id", None) or new_request_id()
        g._exceptionhub_start = time.perf_counter()
        g._exceptionhub_request_id = request_id
        monitor.start_request_context(
            request={
                "method": request.method,
                "url": request.path,
                "request_id": request_id,
            },
            request_id=request_id,
        )

    @app.after_request
    def _sl_finish(response: Any) -> Any:
        from flask import g, request

        start = getattr(g, "_exceptionhub_start", None)
        request_id = getattr(g, "_exceptionhub_request_id", None)
        if start is not None:
            elapsed = time.perf_counter() - start
            response.headers["X-Request-Id"] = request_id
            _report_slow(monitor, request.method, request.path, response.status_code, elapsed, request_id)
        return response

    @app.teardown_request
    def _sl_teardown(exc: Any) -> None:
        # The context is per-request in Flask already; nothing to clean,
        # but this hook is the documented place if that changes.
        del exc

    @app.errorhandler(Exception)
    def _sl_error_handler(error: Exception) -> Any:
        from werkzeug.exceptions import HTTPException

        monitor.capture_exception(
            error,
            severity="critical" if not isinstance(error, HTTPException) else "error",
            tags={"framework": "flask", "http_exception": isinstance(error, HTTPException)},
        )
        if isinstance(error, HTTPException):
            # Flask's make_response() renders HTTP exceptions as their
            # normal error page; returning the exception keeps that behaviour.
            return error
        # Non-HTTP exception: a bare 500 mirrors Flask's default behaviour.
        return app.response_class("Internal Server Error", status=500)

    app.extensions = getattr(app, "extensions", {})
    app.extensions["exceptionhub_monitor"] = monitor
    return monitor


def _report_slow(monitor: Monitor, method: str, url: str, status: int, elapsed: float, request_id: str) -> None:
    from exceptionhub.capture.performance import build_performance_event

    threshold = monitor._config.performance_threshold  # noqa: SLF001
    if elapsed < threshold:
        return
    event = build_performance_event(
        monitor._config,
        f"http {method} {url}",
        elapsed,
        threshold=threshold,
        tags={"framework": "flask"},
        extra={"status_code": status, "request_id": request_id},
    )
    if event is not None:
        event["request"] = {"method": method, "url": url, "status_code": status, "request_id": request_id}
        monitor.capture_event(event, dedupe=False)

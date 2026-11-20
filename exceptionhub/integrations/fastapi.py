"""FastAPI integration: request monitoring middleware + exception capture.

Recommended setup (captures exceptions AND times requests)::

    from fastapi import FastAPI
    from exceptionhub import Monitor
    from exceptionhub.integrations.fastapi import instrument_fastapi

    monitor = Monitor(project_name="fastapi-demo", webhook_url=...)
    monitor.initialize()
    app = FastAPI()
    instrument_fastapi(app, monitor)

Minimal setup (timing/context/slow requests only)::

    app.add_middleware(ExceptionHubMiddleware, monitor=monitor)

Note on Starlette internals: user middleware runs *outside* Starlette's
ExceptionMiddleware, so exceptions raised in routes are converted to
responses before the middleware can see them. ``instrument_fastapi``
therefore also registers app-level exception handlers, which run *inside*
the exception machinery and capture unhandled request exceptions while the
framework's normal 500 / HTTP-error behaviour is preserved.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from exceptionhub.client import Monitor
from exceptionhub.utils.ids import new_request_id


class ExceptionHubMiddleware:
    """ASGI middleware for request timing, correlation id and context.

    - times each request and reports slow ones (threshold from the Monitor);
    - attaches method / endpoint / status / correlation id to events;
    - adds an ``X-Request-Id`` response header.
    """

    def __init__(self, app: Any, monitor: Monitor) -> None:
        self.app = app
        self.monitor = monitor
        if not monitor._initialized:  # noqa: SLF001
            monitor.initialize()

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = scope.get("exceptionhub_request_id") or new_request_id()
        scope["exceptionhub_request_id"] = request_id
        start = time.perf_counter()

        method = scope.get("method", "?")
        path = str(scope.get("path", ""))
        self.monitor.start_request_context(
            request={"method": method, "url": path, "request_id": request_id},
            request_id=request_id,
        )

        status_holder: dict[str, Any] = {}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status_holder["status_code"] = message.get("status")
                headers = message.get("headers") or []
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)

        elapsed = time.perf_counter() - start
        self._report_slow(method, path, elapsed, status_holder.get("status_code"), request_id)

    def _report_slow(self, method: str, path: str, elapsed: float, status: Any, request_id: str) -> None:
        """Report a slow request (threshold from the Monitor config)."""
        from exceptionhub.capture.performance import build_performance_event

        threshold = self.monitor._config.performance_threshold  # noqa: SLF001
        if elapsed < threshold:
            return
        event = build_performance_event(
            self.monitor._config,
            f"http {method} {path}",
            elapsed,
            threshold=threshold,
            tags={"framework": "fastapi"},
            extra={"status_code": status, "request_id": request_id},
        )
        if event is not None:
            event["request"] = {"method": method, "url": path, "status_code": status, "request_id": request_id}
            self.monitor.capture_event(event, dedupe=False)


def instrument_fastapi(app: Any, monitor: Monitor) -> Monitor:
    """Full ExceptionHub setup for a FastAPI/Starlette application.

    1. Adds :class:`ExceptionHubMiddleware` (timing, correlation, slow requests).
    2. Registers app-level exception handlers that capture unhandled
       request exceptions while still returning the framework's normal
       responses (500 for non-HTTP errors, the exception's own status for
       HTTP errors).

    Returns the monitor.
    """
    from starlette.exceptions import HTTPException
    from starlette.responses import Response

    if not monitor._initialized:  # noqa: SLF001
        monitor.initialize()
    app.add_middleware(ExceptionHubMiddleware, monitor=monitor)

    async def http_handler(request: Any, exc: HTTPException) -> Response:
        monitor.capture_exception(
            exc,
            tags={"framework": "fastapi", "http_exception": True},
            extra={"status_code": exc.status_code},
        )
        return Response(status_code=exc.status_code, content=str(exc.detail), media_type="text/plain")

    async def unhandled_handler(request: Any, exc: Exception) -> Response:
        monitor.capture_exception(
            exc,
            severity="critical",
            tags={"framework": "fastapi"},
            extra={"unhandled": True},
        )
        return Response(status_code=500)

    app.add_exception_handler(HTTPException, http_handler)
    app.add_exception_handler(Exception, unhandled_handler)
    return monitor

"""The Monitor client: public entry point of the ExceptionHub SDK."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from exceptionhub.capture.exceptions import (
    UnhandledExceptionHandler,
    build_exception_event,
    thread_unhandled_hook,
)
from exceptionhub.capture.messages import build_custom_event, build_message_event
from exceptionhub.capture.performance import build_performance_event
from exceptionhub.config import MonitorConfig
from exceptionhub.context import ExecutionContext, in_sdk, new_request_context, set_context
from exceptionhub.exceptions import ConfigurationError
from exceptionhub.logging import get_logger
from exceptionhub.transport.webhook import WebhookTransport
from exceptionhub.transport.worker import EventWorker
from exceptionhub.utils.ids import new_event_id
from exceptionhub.utils.sanitization import SensitiveDataSanitizer

__all__ = ["Monitor", "monitor_performance", "set_global_monitor"]


class Monitor:
    """Framework-agnostic monitoring client.

    Example::

        from exceptionhub import Monitor
        monitor = Monitor(project_name="my-app", webhook_url="https://...")
        monitor.initialize()
        monitor.capture_message("hello")
    """

    def __init__(self, config: MonitorConfig | None = None, **config_kwargs: Any) -> None:
        try:
            self._config = config or MonitorConfig(**config_kwargs)
            self._config._validate()
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
        self._log = get_logger(self._config.debug)
        self._initialized = False
        self._transport: WebhookTransport | None = None
        self._worker: EventWorker | None = None
        self._sanitizer = SensitiveDataSanitizer(self._config.sensitive_fields)
        self._seen_event_ids: set[str] = set()
        self._seen_lock = threading.Lock()

    # ------------------------------------------------------------------ init

    def initialize(self, http_client: Any = None) -> Monitor:
        """Start the background worker and install exception hooks.

        Safe to call more than once (subsequent calls are no-ops).
        """
        if self._initialized:
            return self
        self._transport = WebhookTransport(self._config, http_client=http_client)
        self._worker = EventWorker(self._transport, max_queue_size=self._config.max_queue_size)
        self._install_exception_hooks()
        self._initialized = True
        self._log.debug("ExceptionHub initialized for project %s", self._config.project_name)
        return self

    def _install_exception_hooks(self) -> None:
        import sys
        import threading as _th

        if not self._config.capture_exceptions:
            return
        self._original_excepthook = sys.excepthook
        sys.excepthook = UnhandledExceptionHandler(self._report_unhandled, self._original_excepthook)
        self._original_thread_hook = _th.excepthook
        _th.excepthook = thread_unhandled_hook(self._report_unhandled)

    def _report_unhandled(self, exc_value: BaseException, exc_tb: Any) -> None:
        """Capture an unhandled exception without suppressing it."""
        if not self._enabled():
            return
        event = build_exception_event(
            self._config,
            exc_value,
            severity="critical",
            extra={"unhandled": True},
        )
        self._enqueue(event)

    # ------------------------------------------------------------- public API

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def _enabled(self) -> bool:
        return self._config.enabled

    def disable(self) -> None:
        """Turn off monitoring at runtime (e.g. inside tests)."""
        self._config.enabled = False

    def enable(self) -> None:
        self._config.enabled = True

    def capture_exception(self, exception: BaseException | None = None, **kwargs: Any) -> str | None:
        """Capture a raised exception, or the one active in the caller's frame.

        Returns the event id. The original exception is *never* suppressed.
        No-op (returns ``None``) while inside SDK code, so a failing
        transport can never trigger recursive reporting.
        """
        if in_sdk() or not self._enabled() or not self._config.capture_exceptions:
            return None
        event = build_exception_event(self._config, exception, **kwargs)
        self._enqueue(event)
        return event["event_id"]

    def capture_message(self, message: str, **kwargs: Any) -> str | None:
        """Manually report a log-style message. Returns the event id, or ``None``
        when disabled or inside SDK code (recursion guard)."""
        if in_sdk() or not self._enabled():
            return None
        event = build_message_event(self._config, message, **kwargs)
        self._enqueue(event)
        return event["event_id"]

    def capture_event(self, event: dict[str, Any], *, dedupe: bool = True) -> str | None:
        """Send a fully-formed event dict (custom events, pre-built events).

        With ``dedupe`` (default) the same ``event_id`` is sent at most once,
        which prevents duplicate submissions of retried calls.
        """
        if in_sdk() or not self._enabled():
            return None
        event_id = event.get("event_id")
        if event_id is None:
            event = {**event, "event_id": new_event_id()}
            event_id = event["event_id"]
        if dedupe:
            with self._seen_lock:
                if event_id in self._seen_event_ids:
                    return event_id
                self._seen_event_ids.add(event_id)
                # Keep the set from growing without bound.
                if len(self._seen_event_ids) > 10000:
                    self._seen_event_ids = {event_id}
        self._enqueue(self._sanitizer.sanitize(event))
        return event_id

    def capture_custom(self, category: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> str | None:
        """Record a custom application event (DB calls, memory, CPU, ...)."""
        if in_sdk() or not self._enabled():
            return None
        event = build_custom_event(self._config, category, payload, **kwargs)
        self._enqueue(event)
        return event["event_id"]

    # -------------------------------------------------------- performance API

    def performance(
        self,
        operation: str,
        *,
        threshold: float | None = None,
        report_all: bool = False,
        tags: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> PerformanceTimer:
        """Context manager that times *operation* and reports slow runs.

        ::

            with monitor.performance("database_processing", threshold=2.0):
                do_work()
        """
        return PerformanceTimer(self, operation, threshold=threshold, report_all=report_all, tags=tags, extra=extra)

    def performance_timer(self, operation: str, **kwargs: Any) -> PerformanceTimer:
        """Alias for :meth:`performance` (returns the same timer object)."""
        return self.performance(operation, **kwargs)

    # ------------------------------------------------- request context API

    def start_request_context(
        self,
        *,
        request: dict[str, Any] | None = None,
        request_id: str | None = None,
        tags: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ExecutionContext:
        """Bind a request context to the current thread/task.

        Framework integrations use this; standalone code rarely needs it.
        """
        context = new_request_context(request=request, request_id=request_id, tags=tags, extra=extra)
        set_context(context)
        return context

    # ------------------------------------------------------------- transport

    def _enqueue(self, event: dict[str, Any]) -> None:
        if in_sdk():
            # Inside SDK code: suppress so a failing transport can never
            # trigger recursive reporting.
            return
        if self._worker is None:
            # Not initialized: deliver synchronously so events are not lost.
            if self._config.enabled:
                WebhookTransport(self._config).send(event)
            return
        if not self._worker.submit(event):
            self._log.warning("ExceptionHub event %s was not queued", event.get("event_id"))

    def flush(self, timeout: float = 5.0) -> int:
        """Block until the queue is empty (or *timeout* expires).

        Returns the number of events still pending afterwards.
        """
        if self._worker is None:
            return 0
        return self._worker.flush(timeout=timeout)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush pending events and stop the worker. Idempotent."""
        if self._worker is not None:
            self._worker.shutdown(timeout=timeout)
            self._worker = None
        self._restore_exception_hooks()

    def _restore_exception_hooks(self) -> None:
        import sys
        import threading as _th

        if getattr(self, "_original_excepthook", None) is not None:
            sys.excepthook = self._original_excepthook
            _th.excepthook = self._original_thread_hook
            self._original_excepthook = None

    def __repr__(self) -> str:
        state = "initialized" if self._initialized else "uninitialized"
        return f"<Monitor project={self._config.project_name!r} {state}>"


class PerformanceTimer:
    """``time`` a block with :func:`time.perf_counter` and report slow runs.

    Usable as a context manager::

        with monitor.performance("op"):
            work()

    or standalone::

        timer = monitor.performance("op")
        timer.start(); work(); timer.stop_and_report()
    """

    def __init__(
        self,
        monitor: Monitor,
        operation: str,
        *,
        threshold: float | None = None,
        report_all: bool = False,
        tags: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._monitor = monitor
        self._operation = operation
        self._threshold = threshold
        self._report_all = report_all
        self._tags = tags
        self._extra = extra
        self._start: float | None = None

    def __enter__(self) -> PerformanceTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc is not None:
            self._monitor.capture_exception(exc, tags={**(self._tags or {}), "operation": self._operation})
            return None  # never suppress the application exception
        self._finish()
        return None

    def start(self) -> float:
        self._start = time.perf_counter()
        return self._start

    def stop_and_report(self) -> float | None:
        if self._start is None:
            self.start()
        self._finish()
        return None

    def _finish(self) -> None:
        elapsed = time.perf_counter() - (self._start or 0)
        event = build_performance_event(
            self._monitor._config,  # noqa: SLF001 - same package
            self._operation,
            elapsed,
            threshold=self._threshold,
            report_all=self._report_all,
            tags=self._tags,
            extra=self._extra,
        )
        if event is not None:
            self._monitor._enqueue(event)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Module-level decorator, per the API spec. When used without a prior
# Monitor, it attaches timing to the global monitor (if one exists).
# ---------------------------------------------------------------------------

_global_monitor: Monitor | None = None


def monitor_performance(
    func: Callable[..., Any] | None = None,
    *,
    operation: str | None = None,
    threshold: float | None = None,
    report_all: bool = False,
    monitor: Monitor | None = None,
) -> Callable[..., Any]:
    """Decorator that reports slow executions of the wrapped function.

    ::

        from exceptionhub import monitor_performance

        @monitor_performance
        def process_data(): ...

    The operation name defaults to the wrapped function's ``__qualname__``.
    Thread-safe: timing is measured independently in each thread.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        target = monitor or _global_monitor
        op = operation or fn.__qualname__

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if target is None:
                return fn(*args, **kwargs)  # monitoring not set up: run as-is
            timer = target.performance(op, threshold=threshold, report_all=report_all)
            timer.__enter__()
            try:
                return fn(*args, **kwargs)
            finally:
                active_exc = sys.exc_info()[1]
                if active_exc is not None:
                    # Exception propagates unchanged; the timer captures it.
                    timer.__exit__(type(active_exc), active_exc, active_exc.__traceback__)
                else:
                    timer._finish()  # noqa: SLF001 - same package

        return wrapper

    if func is not None and callable(func):
        return decorator(func)
    return decorator


def set_global_monitor(monitor: Monitor) -> None:
    """Register a Monitor for use by bare ``@monitor_performance`` usage."""
    global _global_monitor
    _global_monitor = monitor

"""Tests for exception capture (manual + unhandled) and the recursion guard."""

from __future__ import annotations

import sys

import pytest

from exceptionhub import Monitor


def test_manual_capture_with_exception_object(monitor: Monitor, fake_http: object) -> None:
    try:
        1 / 0
    except ZeroDivisionError as exc:
        event_id = monitor.capture_exception(exc)
    assert event_id
    monitor.flush(2.0)
    body = fake_http.last_body
    assert body["event_type"] == "exception"
    assert body["exception"]["type"] == "ZeroDivisionError"
    assert "division by zero" in body["exception"]["message"]
    assert body["exception"]["function"]
    assert body["exception"]["line_number"]
    assert "Traceback" in body["exception"]["stack_trace"]
    assert body["fingerprint"].startswith("f") or body["fingerprint"]


def test_capture_exception_uses_active_exception(monitor: Monitor, fake_http: object) -> None:
    try:
        int("not a number")
    except ValueError:
        monitor.capture_exception()
    monitor.flush(2.0)
    assert fake_http.last_body["exception"]["type"] == "ValueError"


def test_capture_exception_without_active_raises_cleanly(monitor: Monitor, fake_http: object) -> None:
    # Outside an except-block: no active exception, but the call still works
    # and produces a marker event rather than crashing.
    event_id = monitor.capture_exception()
    assert event_id
    monitor.flush(2.0)
    assert fake_http.last_body["exception"]["type"] == "UnknownException"


def test_unhandled_exception_is_reported_but_not_suppressed(fake_http: object) -> None:
    from exceptionhub import Monitor as MonitorCls

    m = MonitorCls(project_name="unhandled", webhook_url="http://localhost:9/api/events/", webhook_key="k")
    true_original = sys.excepthook
    m.initialize(http_client=fake_http)
    assert sys.excepthook is not true_original, "initialize() must install the excepthook"

    exc = RuntimeError("kaboom")
    try:
        raise exc
    except RuntimeError:
        # Simulate an unhandled top-level exception: the interpreter calls
        # sys.excepthook; we must both report and preserve the original hook.
        sys.excepthook(RuntimeError, exc, exc.__traceback__)
    m.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "exception"]
    assert any(b["exception"]["type"] == "RuntimeError" for b in bodies)
    m.shutdown()
    assert sys.excepthook is true_original, "shutdown() must restore the original excepthook"


def test_capture_exception_does_not_suppress_raise(monitor: Monitor) -> None:
    with pytest.raises(ZeroDivisionError):
        try:
            1 / 0
        except ZeroDivisionError:
            monitor.capture_exception()
            raise  # the original exception must propagate unchanged


def test_recursive_reporting_guard(monitor: Monitor, fake_http: object) -> None:
    # While the SDK is reporting, any further capture must be suppressed so a
    # failing transport cannot create an infinite loop.
    from exceptionhub.context import in_sdk, sdk_guard

    with sdk_guard():
        assert in_sdk() is True
        assert monitor.capture_message("should be suppressed") is None
    assert in_sdk() is False
    monitor.capture_message("this goes through")
    monitor.flush(2.0)
    assert [c["body"].get("message") for c in fake_http.calls] == ["this goes through"]


def test_exception_detail_from_exception(monitor: Monitor) -> None:
    from exceptionhub.schemas.exception import ExceptionDetail

    def boom():
        raise ValueError("deep failure")

    try:
        boom()
    except ValueError as exc:
        detail = ExceptionDetail.from_exception(exc)
    assert detail.type == "ValueError"
    assert detail.function == "boom"
    assert "deep failure" in detail.message
    assert detail.line_number is not None


def test_thread_exception_hook_reports(monitor: Monitor, fake_http: object) -> None:
    import threading

    captured = {}

    def worker() -> None:
        try:
            raise KeyError("thread-bomb")
        except KeyError:
            threading.excepthook(
                {"exc_type": KeyError, "exc_value": sys.exc_info()[1], "traceback": None, "thread": threading.current_thread()}
            )

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "exception"]
    assert any(b["exception"]["type"] == "KeyError" for b in bodies)


def test_exception_event_schema_roundtrip(monitor: Monitor) -> None:
    from exceptionhub.schemas.exception import ExceptionEvent

    event = ExceptionEvent(
        project={"name": "p", "environment": "e", "release": "1"},
        exception={"type": "ValueError", "message": "x"},  # nested dict coerced
    )
    data = event.model_dump()
    assert data["exception"]["type"] == "ValueError"
    assert data["event_type"] == "exception"
    assert data["event_id"].startswith("evt_")

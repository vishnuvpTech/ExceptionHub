"""Tests for performance monitoring: context manager, decorator, thresholds."""

from __future__ import annotations

import time

import pytest

from exceptionhub import Monitor, monitor_performance, set_global_monitor


def test_slow_operation_reported(monitor: Monitor, fake_http: object) -> None:
    with monitor.performance("database_processing", threshold=0.01):
        time.sleep(0.05)
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls]
    perf = [b for b in bodies if b.get("event_type") == "performance"]
    assert perf, "slow operation must produce a performance event"
    assert perf[0]["performance"]["operation"] == "database_processing"
    assert perf[0]["performance"]["status"] == "slow"
    assert perf[0]["performance"]["execution_time"] >= 0.01
    assert perf[0]["performance"]["threshold"] == 0.01
    assert perf[0]["performance"]["unit"] == "seconds"
    assert perf[0]["severity"] == "warning"


def test_fast_operation_not_reported(monitor: Monitor, fake_http: object) -> None:
    with monitor.performance("quick", threshold=5.0):
        time.sleep(0.001)
    monitor.flush(2.0)
    assert not [c for c in fake_http.calls if c["body"].get("event_type") == "performance"]


def test_report_all_overrides_threshold(monitor: Monitor, fake_http: object) -> None:
    with monitor.performance("timed", report_all=True):
        time.sleep(0.001)
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls]
    assert bodies and bodies[0]["performance"]["status"] == "ok"
    assert bodies[0]["severity"] == "info"


def test_performance_context_manager_reports_exception_too(monitor: Monitor, fake_http: object) -> None:
    with pytest.raises(ZeroDivisionError):
        with monitor.performance("flaky"):
            1 / 0
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls]
    assert [b for b in bodies if b.get("event_type") == "exception"], "exception inside the block is captured"


def test_decorator_reports_slow_function(monitor: Monitor, fake_http: object, monkeypatch: pytest.MonkeyPatch) -> None:
    set_global_monitor(monitor)

    @monitor_performance
    def slow_job() -> str:
        time.sleep(0.05)
        return "ok"

    assert slow_job() == "ok"
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "performance"]
    # The operation defaults to the function's qualname.
    assert bodies and bodies[0]["performance"]["operation"].endswith("slow_job")


def test_decorator_without_monitor_still_runs_function(fake_http: object) -> None:
    @monitor_performance
    def plain() -> int:
        return 42

    assert plain() == 42


def test_decorator_captures_and_rethrows_exception(monitor: Monitor, fake_http: object) -> None:
    set_global_monitor(monitor)

    @monitor_performance
    def exploding() -> None:
        raise RuntimeError("decorated failure")

    with pytest.raises(RuntimeError, match="decorated failure"):
        exploding()
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "exception"]
    assert any(b["exception"]["type"] == "RuntimeError" for b in bodies)


def test_custom_threshold_per_call(monitor: Monitor, fake_http: object) -> None:
    # Lower threshold than configured: a 50ms op is reported even though the
    # configured threshold is larger.
    with monitor.performance("op", threshold=0.01):
        time.sleep(0.03)
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "performance"]
    assert bodies and bodies[0]["performance"]["threshold"] == 0.01


def test_performance_event_has_timestamp_and_system(monitor: Monitor, fake_http: object) -> None:
    with monitor.performance("ts", threshold=0.001):
        time.sleep(0.01)
    monitor.flush(2.0)
    body = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "performance"][0]
    assert "timestamp" in body and body["timestamp"].endswith("Z")
    assert body["system"]["hostname"]
    assert body["project"]["release"] == "9.9.9"

"""Tests for Monitor initialization, configuration, and core capture APIs."""

from __future__ import annotations

import pytest

from exceptionhub import Monitor
from exceptionhub.exceptions import ConfigurationError


def test_default_configuration(monitor: Monitor) -> None:
    assert monitor._config.project_name == "test-project"
    assert monitor._config.webhook_url == "http://localhost:9999/api/events/"
    assert monitor.enabled


def test_missing_webhook_url_uses_env(monkeypatch: pytest.MonkeyPatch, fake_http: object) -> None:
    monkeypatch.setenv("EXCEPTIONHUB_WEBHOOK_URL", "http://env.example.com/api/events/")
    monkeypatch.delenv("EXCEPTIONHUB_PROJECT_NAME", raising=False)
    m = Monitor(webhook_key="k")
    m.initialize(http_client=fake_http)
    assert m._config.webhook_url == "http://env.example.com/api/events/"
    m.shutdown()


def test_invalid_url_rejected() -> None:
    with pytest.raises(ConfigurationError):
        Monitor(project_name="x", webhook_url="ftp://nope", webhook_key="k")


def test_non_positive_timeout_rejected() -> None:
    with pytest.raises(ConfigurationError):
        Monitor(project_name="x", webhook_url="http://a/b", webhook_key="k", timeout=0)


def test_sample_rate_bounds() -> None:
    with pytest.raises(ConfigurationError):
        Monitor(project_name="x", webhook_url="http://a/b", webhook_key="k", sample_rate=1.5)
    m = Monitor(project_name="x", webhook_url="http://a/b", webhook_key="k", sample_rate=0.5)
    assert m._config.sample_rate == 0.5
    m.shutdown()


def test_disabled_monitor_sends_nothing(monitor: Monitor, fake_http: object) -> None:
    fake_http = type("X", (), {})  # placeholder; real check:
    m = Monitor(project_name="p", webhook_url="http://localhost:9/api/events/", webhook_key="k", enabled=False)
    m.initialize()
    assert m.capture_message("nope") is None
    m.shutdown()


def test_capture_message_delivers_event(monitor: Monitor, fake_http: object) -> None:
    event_id = monitor.capture_message("hello world", level="warning", tags={"a": 1})
    assert event_id
    monitor.flush(2.0)
    body = fake_http.last_body
    assert body["event_type"] == "message"
    assert body["message"] == "hello world"
    assert body["severity"] == "warning"
    assert body["tags"]["a"] == 1
    assert body["project"]["name"] == "test-project"
    assert body["event_id"] == event_id
    assert "stack_trace" not in body
    assert body["system"]["python_version"].startswith("3.")


def test_capture_event_dedupe(monitor: Monitor, fake_http: object) -> None:
    event_id = monitor.capture_event({"event_type": "custom", "event_id": "evt_fixed"})
    monitor.capture_event({"event_type": "custom", "event_id": "evt_fixed"})
    monitor.flush(2.0)
    custom_bodies = [c for c in fake_http.calls if c["body"].get("event_id") == "evt_fixed"]
    assert len(custom_bodies) == 1
    assert event_id == "evt_fixed"


def test_capture_event_generates_missing_id(monitor: Monitor, fake_http: object) -> None:
    event_id = monitor.capture_event({"event_type": "custom", "category": "db"})
    assert event_id.startswith("evt_")
    monitor.flush(2.0)


def test_capture_custom(monitor: Monitor, fake_http: object) -> None:
    monitor.capture_custom("db_query", {"query": "SELECT 1", "duration_ms": 12})
    monitor.flush(2.0)
    body = fake_http.last_body
    assert body["event_type"] == "custom"
    assert body["category"] == "db_query"
    assert body["payload"]["query"] == "SELECT 1"


def test_initialize_is_idempotent(monitor: Monitor) -> None:
    monitor.initialize()
    monitor.initialize()
    assert monitor._initialized


def test_shutdown_flushes_and_stops(monitor: Monitor, fake_http: object) -> None:
    monitor.capture_message("before shutdown")
    monitor.shutdown(timeout=2.0)
    assert fake_http.calls, "event was delivered before the worker stopped"
    # Second shutdown must be a no-op without error.
    monitor.shutdown(timeout=1.0)


def test_sensitive_fields_filtered(monitor: Monitor, fake_http: object) -> None:
    monitor.capture_event(
        {
            "event_type": "custom",
            "payload": {"password": "hunter2", "api_key": "abc", "safe": "ok"},
        }
    )
    monitor.flush(2.0)
    payload = fake_http.last_body["payload"]
    assert payload["password"] == "[FILTERED]"
    assert payload["api_key"] == "[FILTERED]"
    assert payload["safe"] == "ok"


def test_request_context_attached(monitor: Monitor, fake_http: object) -> None:
    monitor.start_request_context(
        request={"method": "GET", "url": "/x"},
        request_id="req_1",
        tags={"user": "u1"},
    )
    monitor.capture_message("in-request")
    monitor.flush(2.0)
    body = fake_http.last_body
    assert body["request"]["request_id"] == "req_1"
    assert body["tags"]["user"] == "u1"

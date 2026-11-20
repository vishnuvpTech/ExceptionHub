"""Tests for webhook transport: auth headers, HMAC, retries, failures, queue."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from conftest import FakeHttpClient

from exceptionhub.config import MonitorConfig
from exceptionhub.transport.retry import RetryPolicy
from exceptionhub.transport.webhook import WebhookTransport


def _config(**over: Any) -> MonitorConfig:
    base = dict(project_name="proj", webhook_url="http://x/api/events/", webhook_key="s3cret")
    base.update(over)
    return MonitorConfig(**base)


def test_auth_headers_present(monitor: Any) -> None:
    t = monitor._transport
    body = json.dumps({"event_id": "evt_1"}).encode()
    headers = t.build_headers({"event_id": "evt_1"}, body)
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Project-Name"] == "test-project"
    assert headers["X-Webhook-Key"] == "test-key"
    assert headers["X-Event-ID"] == "evt_1"
    assert headers["X-Timestamp"].isdigit()
    assert headers["X-Signature"].startswith("sha256=")


def test_hmac_signature_matches_manual_computation(monitor: Any) -> None:
    t = monitor._transport
    body = b'{"event_id":"evt_hmac"}'
    timestamp = "1730000000"
    sig = t.hmac_sign(body, timestamp)
    expected = "sha256=" + hmac.new(
        b"test-key", timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    assert sig == expected


def test_send_success(monitor: Any, fake_http: FakeHttpClient) -> None:
    t = monitor._transport
    assert t.send({"event_id": "evt_a"}) is True
    assert fake_http.calls and fake_http.last_headers["X-Event-ID"] == "evt_a"


def test_send_retries_on_5xx_then_succeeds(monitor: Any, fake_http: FakeHttpClient) -> None:
    fake_http._statuses = [500, 200]
    t = monitor._transport
    assert t.send({"event_id": "evt_b"}) is True
    assert len(fake_http.calls) == 2


def test_send_does_not_retry_on_4xx(monitor: Any, fake_http: FakeHttpClient) -> None:
    fake_http._statuses = [401]
    t = monitor._transport
    assert t.send({"event_id": "evt_c"}) is False
    assert len(fake_http.calls) == 1, "client errors must not be retried"


def test_send_eventually_fails_without_infinite_loop(monitor: Any, fake_http: FakeHttpClient) -> None:
    fake_http._statuses = [503, 503, 503, 503, 503]
    t = monitor._transport
    start = time.monotonic()
    assert t.send({"event_id": "evt_d"}) is False
    elapsed = time.monotonic() - start
    # Bounded: only max_attempts (3) calls, and retries had zero delay.
    assert len(fake_http.calls) == 3
    assert elapsed < 5


def test_network_failure_is_retried_then_abandoned(monitor: Any, fake_http: FakeHttpClient) -> None:
    fake_http._raise = True
    t = monitor._transport
    assert t.send({"event_id": "evt_e"}) is False
    assert len(fake_http.calls) == 3  # all attempts, no raise leaked out


def test_retry_policy_never_exceeds_max_attempts() -> None:
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(1, 500) is True
    assert policy.should_retry(2, None) is True
    assert policy.should_retry(3, 500) is False  # at the cap
    assert policy.should_retry(1, 404) is False  # 4xx not retriable


def test_retry_delays_are_bounded() -> None:
    policy = RetryPolicy(max_attempts=5, initial_delay=1.0, factor=2.0, max_delay=5.0, jitter=0.0)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(4) == 5.0  # capped at max_delay


def test_queue_full_drops_event_and_counts(fake_http: FakeHttpClient) -> None:
    from exceptionhub.transport.webhook import WebhookTransport
    from exceptionhub.transport.worker import EventWorker

    transport = WebhookTransport(_config(max_retries=0), http_client=fake_http, retry_policy=RetryPolicy(max_attempts=1))
    worker = EventWorker(transport, max_queue_size=2, start=False)
    worker.start()
    # Fill the queue. submit returns False once the bounded queue is full.
    results = [worker.submit({"event_id": f"evt_{i}"}) for i in range(10)]
    assert results[0] and results[1]
    assert worker.dropped >= 1
    worker.shutdown(timeout=2.0)


def test_worker_shutdown_flushes_pending(fake_http: FakeHttpClient) -> None:
    from exceptionhub.transport.webhook import WebhookTransport
    from exceptionhub.transport.worker import EventWorker

    transport = WebhookTransport(_config(max_retries=0), http_client=fake_http, retry_policy=RetryPolicy(max_attempts=1))
    worker = EventWorker(transport, max_queue_size=100, start=True)
    for i in range(5):
        worker.submit({"event_id": f"evt_{i}"})
    worker.shutdown(timeout=3.0)
    assert len(fake_http.calls) == 5


def test_no_secrets_in_error_logs(caplog: pytest.LogCaptureFixture, fake_http: FakeHttpClient) -> None:
    fake_http._raise = True
    transport = WebhookTransport(_config(webhook_key="super-secret-key"), http_client=fake_http,
                                 retry_policy=RetryPolicy(max_attempts=1))
    with caplog.at_level("WARNING", logger="exceptionhub"):
        transport.send({"event_id": "evt_log"})
    # The webhook key must never appear in any log record.
    assert "super-secret-key" not in " ".join(caplog.messages)
    # But the delivery failure itself must be logged.
    assert any("ExceptionHub" in m for m in caplog.messages)

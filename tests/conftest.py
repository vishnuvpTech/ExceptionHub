"""Shared fixtures: a fake HTTP client that records POSTs and a Monitor wired to it."""

from __future__ import annotations

import json
from typing import Any

import pytest


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "{}") -> None:
        self.status_code = status_code
        self.text = text


class FakeHttpClient:
    """Records every POST; scripted responses via ``statuses`` list (default 200)."""

    def __init__(self, statuses: list[int] | None = None, fail_times: int = 0, raise_exception: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._statuses = list(statuses or [])
        self._fail_times = fail_times
        self._raise = raise_exception

    def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "body": json.loads(content), "headers": headers, "timeout": timeout})
        if self._raise:
            raise ConnectionError("simulated network failure")
        if self._fail_times > 0:
            self._fail_times -= 1
            return FakeResponse(503)
        if self._statuses:
            return FakeResponse(self._statuses.pop(0))
        return FakeResponse(200)

    @property
    def last_body(self) -> dict[str, Any]:
        return self.calls[-1]["body"]

    @property
    def last_headers(self) -> dict[str, str]:
        return self.calls[-1]["headers"]


@pytest.fixture
def fake_http() -> FakeHttpClient:
    return FakeHttpClient()


@pytest.fixture
def monitor(fake_http: FakeHttpClient, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A Monitor whose transport uses the fake client, with instant retries."""
    from exceptionhub import Monitor
    from exceptionhub.transport.retry import RetryPolicy

    m = Monitor(
        project_name="test-project",
        webhook_url="http://localhost:9999/api/events/",
        webhook_key="test-key",
        environment="test",
        release="9.9.9",
        performance_threshold=0.01,
        max_retries=2,
    )
    m.initialize(http_client=fake_http)
    # Make retries instant so tests are fast.
    m._transport._retry = RetryPolicy(max_attempts=3, initial_delay=0.0, factor=1.0, jitter=0.0)
    yield m
    m.shutdown(timeout=2.0)


@pytest.fixture(autouse=True)
def _restore_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the unhandled-exception hooks are restored after each test."""
    import sys
    import threading

    original_hook = sys.excepthook
    original_thread_hook = threading.excepthook
    yield
    sys.excepthook = original_hook
    threading.excepthook = original_thread_hook

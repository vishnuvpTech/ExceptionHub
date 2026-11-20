"""Integration tests: FastAPI, Flask and Django middlewares against a live Monitor."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from conftest import FakeHttpClient
from starlette.testclient import TestClient

from exceptionhub import Monitor

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------


def _fastapi_app(monitor: Monitor):
    from fastapi import FastAPI, HTTPException

    from exceptionhub.integrations.fastapi import instrument_fastapi

    app = FastAPI()
    instrument_fastapi(app, monitor)

    @app.get("/ok")
    def ok() -> dict:
        return {"status": "ok"}

    @app.get("/boom")
    def boom() -> None:
        raise HTTPException(status_code=500, detail="nope")

    @app.get("/slow")
    def slow() -> dict:
        time.sleep(0.05)
        return {"status": "slow"}

    @app.get("/capture")
    def capture() -> dict:
        monitor.capture_message("from-request", tags={"marker": "ctx"})
        return {"status": "ok"}

    return app


def test_fastapi_ok_request_sends_no_events(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    client = TestClient(_fastapi_app(monitor))
    response = client.get("/ok")
    assert response.status_code == 200
    assert "x-request-id" in {k.lower() for k in response.headers}, "correlation id must be returned"
    monitor.flush(2.0)
    assert not [c for c in fake_http.calls if c["body"].get("event_type") == "exception"]


def test_fastapi_unhandled_exception_captured(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    client = TestClient(_fastapi_app(monitor), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "exception"]
    assert bodies, "FastAPI 500 must produce an exception event"
    assert bodies[0]["exception"]["type"] == "HTTPException"
    assert bodies[0]["tags"]["framework"] == "fastapi"
    assert bodies[0]["request"]["request_id"], "request correlation id must be attached"


def test_fastapi_slow_request_captured(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    monitor._config.performance_threshold = 0.01  # noqa: SLF001 - make /slow trigger
    client = TestClient(_fastapi_app(monitor))
    client.get("/slow")
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "performance"]
    assert bodies, "slow FastAPI request must produce a performance event"
    assert bodies[0]["performance"]["operation"] == "http GET /slow"
    assert bodies[0]["request"]["request_id"]
    assert bodies[0]["request"]["status_code"] == 200


def test_fastapi_request_context_attached(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    """A capture inside a request must carry that request's context (no leakage)."""
    client = TestClient(_fastapi_app(monitor))
    client.get("/capture")
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "message"]
    assert bodies
    assert bodies[0]["tags"]["marker"] == "ctx"
    assert bodies[0]["request"]["request_id"], "request context must leak into events in the same task"


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------


def test_flask_unhandled_error_captured(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    from flask import Flask

    from exceptionhub.integrations.flask import instrument_flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    instrument_flask(app, monitor)

    @app.route("/explode")
    def explode() -> None:
        raise RuntimeError("flask kaboom")

    response = app.test_client().get("/explode")
    assert response.status_code == 500
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "exception"]
    assert bodies, "Flask unhandled error must be captured"
    assert bodies[0]["exception"]["type"] == "RuntimeError"
    assert bodies[0]["tags"]["framework"] == "flask"


def test_flask_slow_request_reported(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    from flask import Flask

    from exceptionhub.integrations.flask import instrument_flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    instrument_flask(app, monitor)
    monitor._config.performance_threshold = 0.01  # noqa: SLF001

    @app.route("/slow")
    def slow() -> str:
        time.sleep(0.05)
        return "done"

    response = app.test_client().get("/slow")
    assert response.status_code == 200
    assert "X-Request-Id" in response.headers
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "performance"]
    assert bodies and bodies[0]["performance"]["operation"] == "http GET /slow"
    assert bodies[0]["request"]["status_code"] == 200


def test_flask_context_does_not_leak_between_requests(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    from flask import Flask

    from exceptionhub.integrations.flask import instrument_flask

    app = Flask(__name__)
    instrument_flask(app, monitor)

    @app.route("/a")
    def a() -> str:
        return "a"

    @app.route("/b")
    def b() -> str:
        return "b"

    client = app.test_client()
    response_a = client.get("/a")
    id_a = response_a.headers["X-Request-Id"]
    response_b = client.get("/b")
    id_b = response_b.headers["X-Request-Id"]
    assert id_a and id_b
    assert id_a != id_b, "each request gets its own correlation id"


# ---------------------------------------------------------------------------
# Django
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def django_app() -> None:
    """Configure a minimal Django settings module once for the module."""
    import django
    from django.conf import settings

    if settings.configured:
        return
    settings.configure(
        DEBUG=False,
        SECRET_KEY="test",
        ALLOWED_HOSTS=["testserver"],
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "exceptionhub.integrations.django"],
        DATABASES={},
        EXCEPTIONHUB={},
    )
    django.setup()
    from django.core.signals import got_request_exception

    # The app config's ready() would have connected the signal in a real
    # project; do it explicitly here because settings.configure() does not
    # run the full app-loading machinery for our module-style app.
    from exceptionhub.integrations.django import _on_request_exception

    got_request_exception.connect(_on_request_exception, dispatch_uid="exceptionhub.got_request_exception")


class _FakeRequest:
    def __init__(self, method: str = "GET", path: str = "/") -> None:
        self.method = method
        self.path = path
        self.headers = {}
        self.META = {}
        self._exceptionhub_start = None
        self._exceptionhub_request_id = None


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers: dict = {}


def test_django_middleware_captures_exception(django_app: None, monitor: Monitor, fake_http: FakeHttpClient) -> None:
    from exceptionhub.integrations.django import ExceptionHubMiddleware, register_monitor

    register_monitor(monitor)
    middleware = ExceptionHubMiddleware(get_response=lambda request: _FakeResponse(500))

    request = _FakeRequest()

    try:
        raise ValueError("django boom")
    except ValueError:
        middleware.process_exception(request, _active_exception())

    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "exception"]
    assert bodies, "middleware.process_exception must capture the exception"
    assert bodies[0]["tags"]["framework"] == "django"


def test_django_middleware_times_and_reports_slow_request(django_app: None, monitor: Monitor, fake_http: FakeHttpClient) -> None:
    from exceptionhub.integrations.django import ExceptionHubMiddleware, register_monitor

    register_monitor(monitor)
    monitor._config.performance_threshold = 0.01  # noqa: SLF001

    def get_response(request: _FakeRequest) -> _FakeResponse:
        time.sleep(0.05)
        return _FakeResponse(200)

    middleware = ExceptionHubMiddleware(get_response=get_response)
    response = middleware(_FakeRequest())
    assert response.status_code == 200
    assert "X-Request-Id" in response.headers
    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "performance"]
    assert bodies, "slow Django request must produce a performance event"
    assert bodies[0]["tags"]["framework"] == "django"


def test_django_request_unhandled_signal_reports(django_app: None, fake_http: FakeHttpClient) -> None:
    """The signal wired by the Django app reports unhandled request errors."""
    from django.core.signals import got_request_exception

    import exceptionhub.integrations.django as django_integration

    monitor = Monitor(
        project_name="django-signal",
        webhook_url="http://localhost:9/api/events/",
        webhook_key="k",
    )
    monitor.initialize(http_client=fake_http)
    django_integration.register_monitor(monitor)

    try:
        raise RuntimeError("django unhandled")
    except RuntimeError:
        # Django emits this signal while the exception is still the active
        # one (sys.exc_info()), which is exactly what the handler reads.
        got_request_exception.send(
            sender="test",
            request=_FakeRequest(),
            response=_FakeResponse(500),
        )

    monitor.flush(2.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "exception"]
    assert any(b["exception"]["type"] == "RuntimeError" for b in bodies)
    assert any(b["exception"]["type"] == "RuntimeError" and b["extra"].get("unhandled") for b in bodies)
    monitor.shutdown()


def _active_exception() -> BaseException:
    import sys

    return sys.exc_info()[1]


# ---------------------------------------------------------------------------
# Receiver contract: every event shape the SDK produces is accepted by the
# reference receiver (auth + HMAC + replay window + schema validation).
# ---------------------------------------------------------------------------


def test_receiver_accepts_all_event_shapes(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    from examples.webhook_receiver.main import PROJECT_KEYS, app

    project_key = PROJECT_KEYS["resource-augmentation"]

    monitor.capture_message("contract check")
    try:
        raise ValueError("contract check")
    except ValueError:
        monitor.capture_exception()
    monitor.flush(2.0)
    assert len(fake_http.calls) >= 2

    client = TestClient(app)
    for call in fake_http.calls:
        body = json.dumps(call["body"]).encode("utf-8")
        # Rebuild exactly the headers the SDK transport would send.
        timestamp = str(int(time.time()))
        signature = "sha256=" + hmac.new(
            project_key.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        response = client.post(
            "/api/events/",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Project-Name": "resource-augmentation",
                "X-Webhook-Key": project_key,
                "X-Timestamp": timestamp,
                "X-Event-ID": call["body"]["event_id"],
                "X-Signature": signature,
            },
        )
        assert response.status_code == 200, response.text


def test_receiver_rejects_bad_key(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    from examples.webhook_receiver.main import PROJECT_KEYS, app

    project_key = PROJECT_KEYS["resource-augmentation"]
    monitor.capture_message("reject me")
    monitor.flush(2.0)
    call = fake_http.calls[0]
    body = json.dumps(call["body"]).encode("utf-8")
    timestamp = str(int(time.time()))
    # Sign with the correct key but send a wrong X-Webhook-Key.
    signature = "sha256=" + hmac.new(
        project_key.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    client = TestClient(app)
    response = client.post(
        "/api/events/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Project-Name": "resource-augmentation",
            "X-Webhook-Key": "wrong-key",
            "X-Timestamp": timestamp,
            "X-Event-ID": call["body"]["event_id"],
            "X-Signature": signature,
        },
    )
    assert response.status_code == 401


def test_receiver_rejects_stale_timestamp(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    from examples.webhook_receiver.main import PROJECT_KEYS, app

    project_key = PROJECT_KEYS["resource-augmentation"]
    monitor.capture_message("old timestamp")
    monitor.flush(2.0)
    call = fake_http.calls[0]
    body = json.dumps(call["body"]).encode("utf-8")
    stale = str(int(time.time()) - 9999)
    signature = "sha256=" + hmac.new(
        project_key.encode(), stale.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    client = TestClient(app)
    response = client.post(
        "/api/events/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Project-Name": "resource-augmentation",
            "X-Webhook-Key": project_key,
            "X-Timestamp": stale,
            "X-Event-ID": call["body"]["event_id"],
            "X-Signature": signature,
        },
    )
    assert response.status_code == 403

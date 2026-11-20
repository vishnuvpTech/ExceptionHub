# ExceptionHub

A lightweight, framework-agnostic Python monitoring SDK inspired by Sentry.

ExceptionHub captures application exceptions and performance issues and sends
structured JSON events to a central webhook. The core SDK has no framework
dependencies; Django, FastAPI and Flask integrations are opt-in extras.

```
Application → ExceptionHub SDK → Event Processor → JSON Serializer → HTTP Transport → Webhook API → Storage
```

## Features

- **Exception monitoring** — unhandled (via `sys.excepthook` +
  `threading.excepthook`), manually captured, and framework-request
  exceptions, with type, message, full stack trace, file/function/line and
  a stable **fingerprint** for grouping similar errors.
- **Performance monitoring** — a `@monitor_performance` decorator and a
  `monitor.performance(...)` context manager that report slow operations
  against a configurable threshold.
- **Framework integrations** — Django, FastAPI and Flask. All use the same
  central client and event format.
- **Safe delivery** — bounded background queue, non-blocking delivery,
  bounded retries with exponential backoff, graceful shutdown/flush,
  recursive-reporting guard, and a design that ensures **monitoring
  failures never crash the host application**.
- **Security** — per-project webhook keys, HMAC-SHA256 request signing,
  timestamp + replay-window validation, sensitive-field filtering
  (passwords, tokens, keys are redacted before sending), and no secrets in
  logs.

## Installation

Requires Python 3.10+.

```bash
pip install exceptionhub

# Framework extras (optional)
pip install "exceptionhub[fastapi]" "exceptionhub[flask]" "exceptionhub[django]"
# or everything + dev tools
pip install "exceptionhub[all]" "exceptionhub[dev]"
```

The core only needs `pydantic` and `httpx`.

## Architecture

| Module | Responsibility |
|---|---|
| `exceptionhub/config.py` | `MonitorConfig` — validation + env-var resolution |
| `exceptionhub/client.py` | `Monitor` — the public API; `monitor_performance` decorator |
| `exceptionhub/context.py` | Per-request/thread context (contextvars) + recursion guard |
| `exceptionhub/capture/` | Event builders: `exceptions.py`, `performance.py`, `messages.py` |
| `exceptionhub/transport/` | `webhook.py` (HTTPX + HMAC + headers), `retry.py` (backoff policy), `worker.py` (bounded queue + thread) |
| `exceptionhub/schemas/` | Pydantic event models (`BaseEvent`, `ExceptionEvent`, `PerformanceEvent`, …) |
| `exceptionhub/utils/` | ids, fingerprints, sanitization, serialization, system info |
| `exceptionhub/integrations/` | `django.py`, `fastapi.py`, `flask.py` (optional) |

Event flow: `Monitor.capture_*` → event builder (Pydantic schema +
fingerprint + sanitization) → bounded queue → background worker →
`WebhookTransport.send` (bounded retries, HMAC-signed) → webhook receiver.

## Quick start (standalone Python)

```python
from exceptionhub import Monitor, monitor_performance, set_global_monitor

monitor = Monitor(
    project_name="resource-augmentation",
    webhook_url="https://monitor.example.com/api/events/",
    webhook_key="PROJECT_WEBHOOK_SECRET",
    environment="production",
    release="1.0.0",
    performance_threshold=2.0,
    timeout=5,
)
monitor.initialize()
set_global_monitor(monitor)  # enables bare @monitor_performance

monitor.capture_message("deployment finished")

try:
    do_risky_thing()
except Exception:
    monitor.capture_exception()          # active exception, or pass one explicitly

@monitor_performance                    # reports when the call is slower than the threshold
def process_data():
    ...
```

### Configuration

Every parameter can come from constructor kwargs **or** environment
variables (kwargs win). `EXCEPTIONHUB_` is the prefix:

| Parameter | Env var | Default |
|---|---|---|
| `project_name` | `EXCEPTIONHUB_PROJECT_NAME` | `APP_NAME`, else `unknown-project` |
| `webhook_url` | `EXCEPTIONHUB_WEBHOOK_URL` | `http://localhost:8000/api/events/` |
| `webhook_key` | `EXCEPTIONHUB_WEBHOOK_KEY` | `insecure-default-key` |
| `environment` | `EXCEPTIONHUB_ENVIRONMENT` | `development` |
| `release` | `EXCEPTIONHUB_RELEASE` | `0.0.0` |
| `capture_exceptions` | `EXCEPTIONHUB_CAPTURE_EXCEPTIONS` | `True` |
| `capture_performance` | `EXCEPTIONHUB_CAPTURE_PERFORMANCE` | `True` |
| `performance_threshold` | `EXCEPTIONHUB_PERFORMANCE_THRESHOLD` | `2.0` |
| `timeout` | `EXCEPTIONHUB_TIMEOUT` | `5.0` |
| `debug` | `EXCEPTIONHUB_DEBUG` | `False` |
| `enabled` | `EXCEPTIONHUB_ENABLED` | `True` |
| `max_queue_size` | `EXCEPTIONHUB_MAX_QUEUE_SIZE` | `1000` |
| `max_retries` | `EXCEPTIONHUB_MAX_RETRIES` | `3` |
| `sample_rate` | `EXCEPTIONHUB_SAMPLE_RATE` | `1.0` |
| `sensitive_fields` | – | password, token, api_key, … |

### Exception monitoring

```python
monitor.capture_exception()                 # the exception active in this frame
monitor.capture_exception(exc)              # an explicit exception object
monitor.capture_message("Something went wrong", level="error", tags={"unit": "billing"})
```

Unhandled top-level and thread exceptions are captured automatically once
`initialize()` has run (via `sys.excepthook` and `threading.excepthook`).
The original traceback is still printed and the original hooks are restored
by `monitor.shutdown()`.

### Performance monitoring

```python
# Context manager (threshold defaults to the Monitor's performance_threshold)
with monitor.performance("database_processing", threshold=1.5):
    do_work()

# Or monitor every execution (not just slow ones), e.g. in development
with monitor.performance("database_processing", report_all=True):
    do_work()

# Decorator (operation name defaults to the function's qualname)
@monitor_performance
def process_data():
    ...
```

The timer never suppresses exceptions: a failure inside the block is
captured **and** re-raised unchanged.

### Custom events

```python
monitor.capture_custom(
    "db_query",
    {"sql": "SELECT 1", "duration_ms": 42},
    tags={"table": "users"},
)
```

### Disabling monitoring at runtime

```python
monitor.disable()    # e.g. inside unit tests
monitor.enable()
```

## Framework integrations

### Django

1. Add the app (wires the `got_request_exception` signal automatically):

    ```python
    INSTALLED_APPS = [
        # ...
        "exceptionhub.integrations.django",
    ]
    ```

2. Optionally add the middleware for request timing / correlation:

    ```python
    MIDDLEWARE = [
        # ...
        "exceptionhub.integrations.django.ExceptionHubMiddleware",
    ]
    ```

3. Configure (from `settings.py` or before the app loads):

    ```python
    EXCEPTIONHUB = {
        "project_name": "django-demo",
        "webhook_url": "https://monitor.example.com/api/events/",
        "webhook_key": "PROJECT_WEBHOOK_SECRET",
        "performance_threshold": 2.0,
    }
    ```

    The app's `ready()` hook and the middleware both lazily register the
    monitor, reading `settings.EXCEPTIONHUB` when no explicit
    `register_monitor(monitor)` has been called.

See `examples/django_app/run.py` for a complete, runnable example.

### FastAPI

```python
from fastapi import FastAPI
from exceptionhub import Monitor
from exceptionhub.integrations.fastapi import instrument_fastapi

monitor = Monitor(project_name="fastapi-demo", webhook_url="...")
app = FastAPI()
instrument_fastapi(app, monitor)
```

`instrument_fastapi` adds the ASGI middleware (timing, `X-Request-Id`
correlation, slow-request reports) **and** app-level exception handlers
that capture unhandled request exceptions while FastAPI still renders its
normal 500/HTTP-error responses. (Starlette runs user middleware outside
its exception machinery, so a bare `app.add_middleware` alone cannot see
request exceptions — use `instrument_fastapi` for full coverage, or
`add_middleware(ExceptionHubMiddleware, monitor=monitor)` for timing only.)

### Flask

```python
from flask import Flask
from exceptionhub import Monitor
from exceptionhub.integrations.flask import instrument_flask

app = Flask(__name__)
instrument_flask(app, Monitor(project_name="flask-demo", webhook_url="..."))
```

`instrument_flask` registers an error handler (captures unhandled errors,
preserves Flask's normal responses), request timing via
`before_request`/`after_request`, per-request correlation ids, and slow
request detection.

## Webhook payload

Every event is a JSON document validated by Pydantic before it leaves the
SDK. Exception events:

```json
{
  "event_id": "evt_123456",
  "event_type": "exception",
  "project": {"name": "resource-augmentation", "environment": "production", "release": "1.0.0"},
  "timestamp": "2026-11-20T10:30:00Z",
  "severity": "critical",
  "exception": {
    "type": "ZeroDivisionError",
    "message": "division by zero",
    "filename": "services/calculation.py",
    "function": "calculate",
    "line_number": 25,
    "stack_trace": "Full traceback"
  },
  "request": {"method": "POST", "url": "/api/resources/", "status_code": 500, "request_id": "req_123"},
  "system": {"hostname": "app-server-01", "python_version": "3.12", "os": "Linux"},
  "tags": {"module": "resource-management"},
  "fingerprint": "7459384c…"
}
```

Performance events:

```json
{
  "event_id": "evt_789012",
  "event_type": "performance",
  "project": {"name": "resource-augmentation", "environment": "production", "release": "1.0.0"},
  "timestamp": "2026-11-20T10:35:00Z",
  "severity": "warning",
  "performance": {"operation": "process_resource", "execution_time": 3.02, "threshold": 2.0, "unit": "seconds", "status": "slow"},
  "system": {"hostname": "app-server-01", "python_version": "3.12", "os": "Linux"},
  "fingerprint": "2418d882…"
}
```

### Request headers

```http
Content-Type: application/json
X-Project-Name: resource-augmentation
X-Webhook-Key: PROJECT_WEBHOOK_SECRET
X-Event-ID: evt_123456
X-Timestamp: 1795123800
X-Signature: sha256=<hmac of "X-Timestamp.<body>" with the project key>
```

### Authentication and security

- **Per-project webhook keys** — the receiver authenticates every event
  against the key configured for that project.
- **HMAC-SHA256 signing** — the receiver recomputes `sha256(timestamp.body)`
  with the project key and requires a match (constant-time comparison).
- **Replay protection** — requests whose `X-Timestamp` is outside a
  configurable window (default 5 min) are rejected with 403.
- **HTTPS in production** — the SDK validates the URL scheme; use
  `https://` for anything but local testing.
- **Secret hygiene** — the webhook key never appears in logs; sensitive
  fields in payloads (`password`, `token`, `api_key`, …, configurable via
  `sensitive_fields`) are replaced with `[FILTERED]` before sending.

### Webhook receiver

A reference receiver is included at `examples/webhook_receiver/main.py`
(FastAPI): it authenticates projects, verifies the HMAC signature and
replay window, validates the event schema, and keeps a bounded in-memory
store. In production, persist events (DB/object store) and load keys from
a secret store.

```bash
pip install fastapi uvicorn
uvicorn main:app --port 8000
```

## Reliability guarantees

| Guarantee | How |
|---|---|
| Monitoring never crashes the app | every SDK code path is wrapped; capture APIs return `None` on internal failure instead of raising |
| Webhook failures never block indefinitely | background worker + bounded retries (default 3) + per-request timeout |
| No unbounded memory growth | fixed-size queue (`max_queue_size`); events are dropped and logged when full |
| No infinite retry loop | `RetryPolicy` has a hard attempt cap; 4xx responses are never retried |
| No duplicate submissions | `capture_event` dedupes on `event_id` by default |
| No recursive reporting | a contextvar guard suppresses capture calls made from inside SDK code |
| No cross-request context leakage | request state lives in `contextvars`, scoped per task/thread |
| Graceful shutdown | `monitor.shutdown()` / `monitor.flush(timeout)` drain the queue with a hard time bound |

## Examples

| Path | What it shows |
|---|---|
| `examples/standalone/app.py` | Manual capture, perf decorator, unhandled-exception hook |
| `examples/fastapi_app/main.py` | FastAPI with `instrument_fastapi` |
| `examples/flask_app/app.py` | Flask with `instrument_flask` |
| `examples/django_app/run.py` | Minimal Django app with app + middleware integration |
| `examples/webhook_receiver/main.py` | FastAPI receiver with HMAC/auth validation |

Run them all together:

```bash
pip install -e ".[all]"
uvicorn examples/webhook_receiver/main:app --port 8000 &   # receiver
python examples/standalone/app.py                            # standalone
python examples/fastapi_app/...  (uvicorn, port 8001)
python examples/flask_app/app.py                             # Flask, port 8002
python examples/django_app/run.py                            # Django, port 8003
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest            # unit + integration tests (mocked HTTP, TestClient)
ruff check .      # lint
bandit -r exceptionhub   # security scan
pip wheel .       # buildable distribution
```

Test coverage includes: initialization and invalid config, manual +
unhandled exception capture, performance decorator/context/thresholds,
JSON serialization, webhook auth headers, HMAC generation, retry policy
and network-failure handling, queue drops/flush, sensitive-data
filtering, Django/FastAPI/Flask integrations, receiver auth/replay
validation, and concurrent (threaded) event processing.

## API reference

```python
Monitor(config=None, **config_kwargs)   # constructor accepts MonitorConfig fields
m.initialize(http_client=None)          # start worker + install exception hooks
m.capture_exception(exc=None, **kw)     # -> event_id | None
m.capture_message(text, **kw)           # -> event_id | None
m.capture_event(dict, dedupe=True)      # -> event_id
m.capture_custom(category, payload, **kw)
m.performance(op, threshold=None, report_all=False, tags=..., extra=...)  # context manager
m.start_request_context(request=..., request_id=..., tags=..., extra=...)
m.flush(timeout=5.0)                    # drain the queue
m.shutdown(timeout=5.0)                 # flush + stop + restore hooks
m.disable() / m.enable()

monitor_performance(func=None, *, operation=None, threshold=None,
                    report_all=False, monitor=None)   # decorator
set_global_monitor(monitor)               # target for bare @monitor_performance

UnhandledExceptionHandler, RetryPolicy, WebhookTransport, EventWorker
ExceptionEvent, PerformanceEvent, MessageEvent, CustomEvent   # Pydantic schemas
```

## Extensibility

The event schema and capture layer are deliberately open:
`CustomEvent` + `capture_custom(category, payload)` is the extension point
for DB-query timing, external API calls, memory/CPU sampling, breadcrumbs
and distributed tracing — each new kind is a new `category` plus, if
needed, a new schema subclass. Sampling (`sample_rate`) and exception
grouping (fingerprint overrides) hooks are already in place.

## License

MIT — see [LICENSE](LICENSE).

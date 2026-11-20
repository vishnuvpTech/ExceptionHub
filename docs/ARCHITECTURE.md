# ExceptionHub Architecture

## Event pipeline

```
 capture_* / framework integrations
        │  build event (Pydantic schema + fingerprint + context merge)
        ▼
   SensitiveDataSanitizer          (redact password/token/key values)
        ▼
   Monitor._enqueue              (recursion guard: no-op inside SDK code)
        ▼
   EventWorker.bounded queue     (drop + log when full; never blocks the app)
        ▼
   EventWorker thread            (single consumer, daemon)
        ▼
   WebhookTransport.send         (HTTPX POST, HMAC-SHA256, auth headers,
                                  bounded retries w/ exponential backoff)
        ▼
   webhook receiver              (auth, replay window, schema check, store)
```

## Design decisions

| Concern | Decision |
|---|---|
| **Crash safety** | Every internal path is wrapped; capture APIs return `None`/`False` instead of raising. `sys.excepthook`/`threading.excepthook` are *chained*, never replaced — the app's normal crash behaviour is preserved. |
| **Recursion guard** | A `ContextVar` (`in_sdk()`) marks SDK-internal code. Public `capture_*` calls made from inside the SDK become no-ops, so a failing transport can never trigger infinite exception reporting. |
| **Context isolation** | Request state lives in a `ContextVar` holding a *new* `ExecutionContext` per call site (`default=None`, materialised on demand). Threads, asyncio tasks and greenlets each see only their own values — no cross-request leakage. |
| **Bounded memory** | The worker queue has a fixed size (`max_queue_size`). Overflowing events are dropped and counted (`worker.dropped`), so an unreachable webhook cannot grow memory without limit. |
| **Bounded time** | Retries have a hard attempt cap (`max_retries` + 1 total); 4xx responses are never retried. `flush()`/`shutdown()` use a helper thread around `queue.Queue.join()` so the wait is timeout-bounded and application shutdown cannot hang. |
| **Duplicates** | `capture_event(dedupe=True)` remembers sent `event_id`s (capped set); the receiver can dedupe on `X-Event-ID` too. |
| **Secrets** | The webhook key only appears in request headers, never in logs. Payload fields whose normalised names match `sensitive_fields` (default: password, token, api_key, …) are replaced with `[FILTERED]` before sending; embedded `Bearer …` / `key=…` strings in values are redacted as well. |
| **Extensibility** | New signal types add a `CustomEvent` category + optional schema subclass; the receiver validates on `event_type`. `BaseEvent` uses `extra="allow"` so receivers and the SDK can grow independently. `sample_rate` and fingerprint overrides are already in place. |

## Security model (wire format)

Every event POST carries:

```
X-Project-Name, X-Webhook-Key, X-Event-ID,
X-Timestamp            (unix seconds)
X-Signature: sha256=HMAC-SHA256(key, timestamp + "." + body)
```

The receiver must: authenticate the project key, recompute and
constant-time-compare the signature, and reject timestamps outside the
replay window (default 300 s). This gives integrity (tampered bodies are
rejected), authenticity (only key holders can post) and replay protection
(replayed requests fail the timestamp window).

## Framework integration notes

- **FastAPI/Starlette** — user middleware runs *outside* the framework's
  exception handlers, so `ExceptionHubMiddleware` alone cannot see route
  exceptions. `instrument_fastapi()` additionally registers app-level
  exception handlers (HTTP errors keep their status; anything else becomes
  a 500) that capture the exception before the response is produced.
- **Flask** — a catch-all `errorhandler(Exception)` captures errors; HTTP
  exceptions are returned to `make_response` untouched, non-HTTP errors
  become a bare 500 (matching Flask's default behaviour).
- **Django** — the app (`INSTALLED_APPS`) connects Django's
  `got_request_exception` signal (older releases: `request_unhandled`);
  the optional middleware adds timing, correlation ids and the HTTP
  context to events.

## Testing strategy

- Unit tests mock the HTTP layer with `FakeHttpClient`; no network needed.
- Framework tests use each framework's test client (`TestClient` /
  `app.test_client()` / faked Django requests) against a real `Monitor`.
- The receiver is tested as a service: valid events accepted, wrong key
  → 401, stale timestamp → 403, malformed body → 422.
- Concurrency tests exercise 20–30 threads submitting events in parallel
  and assert no event is lost or duplicated.

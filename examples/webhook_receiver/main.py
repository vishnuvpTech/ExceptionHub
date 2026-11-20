"""ExceptionHub webhook receiver: a minimal FastAPI service that validates and
stores monitoring events.

Run::

    uvicorn main:app --host 0.0.0.0 --port 8000

Configure projects via the ``PROJECT_KEYS`` dictionary below, or through
the ``EXCEPTIONHUB_PROJECT_KEYS`` env var (JSON map of project_name -> key).
In production use HTTPS and persist events somewhere durable; this example
keeps them in an in-memory buffer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

#: project name -> shared webhook key. In real deployments load this from
#: a database / secret store.
PROJECT_KEYS: dict[str, str] = json.loads(os.environ.get("EXCEPTIONHUB_PROJECT_KEYS", "{}") or "{}")
# Defaults so the bundled examples work out of the box.
PROJECT_KEYS.setdefault("resource-augmentation", "PROJECT_WEBHOOK_SECRET")
PROJECT_KEYS.setdefault("fastapi-demo", "PROJECT_WEBHOOK_SECRET")
PROJECT_KEYS.setdefault("flask-demo", "PROJECT_WEBHOOK_SECRET")
PROJECT_KEYS.setdefault("django-demo", "PROJECT_WEBHOOK_SECRET")
#: Maximum age (seconds) accepted for the X-Timestamp header (replay window).
REPLAY_WINDOW_SECONDS = 300

app = FastAPI(title="ExceptionHub Webhook Receiver")

# In-memory event store (bounded) for demonstration purposes.
_EVENT_STORE: deque[dict[str, Any]] = deque(maxlen=10_000)


def _verify_hmac(body: bytes, signature: str, timestamp: str, key: str) -> bool:
    """Recompute the SDK's signature and require a constant-time match."""
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(key.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[len("sha256="):])


def _check_replay_window(timestamp: str) -> None:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Missing or invalid X-Timestamp header")
    age = abs(int(time.time()) - ts)
    if age > REPLAY_WINDOW_SECONDS:
        raise HTTPException(status_code=403, detail="Request timestamp outside accepted window (possible replay)")


@app.post("/api/events/")
async def receive_event(
    request: Request,
    x_project_name: str | None = Header(default=None),
    x_webhook_key: str | None = Header(default=None),
    x_event_id: str | None = Header(default=None),
    x_timestamp: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    body = await request.body()

    # 1. Authenticate the project.
    if not x_project_name or not x_webhook_key:
        raise HTTPException(status_code=401, detail="Missing project credentials")
    expected_key = PROJECT_KEYS.get(x_project_name)
    if expected_key is None:
        raise HTTPException(status_code=401, detail="Unknown project")
    if not hmac.compare_digest(expected_key, x_webhook_key):
        raise HTTPException(status_code=401, detail="Invalid webhook key")

    # 2. Validate the HMAC signature and the replay window.
    if not _verify_hmac(body, x_signature or "", x_timestamp or "", expected_key):
        raise HTTPException(status_code=401, detail="Invalid request signature")
    _check_replay_window(x_timestamp or "")

    # 3. Validate the payload against the event schema.
    try:
        event = json.loads(body)
        assert event.get("event_id") and event.get("event_type") and event.get("project")
    except (json.JSONDecodeError, AssertionError) as exc:
        raise HTTPException(status_code=422, detail=f"Malformed event payload: {exc}") from exc

    _EVENT_STORE.append(
        {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "x_event_id": x_event_id or event.get("event_id"),
            "event": event,
        }
    )
    return {"ok": True, "event_id": event.get("event_id"), "stored": len(_EVENT_STORE)}


@app.get("/api/events/")
async def list_events(limit: int = 100) -> list[dict[str, Any]]:
    """Read back stored events (newest last, bounded)."""
    return list(_EVENT_STORE)[-max(1, min(limit, 1000)):]


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "events_stored": len(_EVENT_STORE), "projects": list(PROJECT_KEYS)}

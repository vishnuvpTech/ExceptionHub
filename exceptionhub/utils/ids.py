"""ID and timestamp helpers."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone


def new_event_id() -> str:
    """Return a unique, sortable event id like ``evt_<16 hex>``."""
    return "evt_" + secrets.token_hex(8)


def new_request_id() -> str:
    """Return a correlation id like ``req_<12 hex>``."""
    return "req_" + secrets.token_hex(6)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

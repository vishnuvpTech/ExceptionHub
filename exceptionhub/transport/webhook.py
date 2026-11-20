"""Webhook transport: HTTPX POST of event JSON with auth headers and HMAC.

Design notes:
- :meth:`WebhookTransport.send` never raises: every failure path is logged
  and reported back as ``False`` so monitoring can never crash the host app.
- The HTTP client is injectable (``http_client``) for tests.
- The webhook key is only ever placed in request headers; it is never
  written to logs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Protocol

from exceptionhub.config import MonitorConfig
from exceptionhub.logging import get_logger
from exceptionhub.transport.retry import RetryPolicy


class _HttpClient(Protocol):
    """Minimal interface so tests can substitute a fake HTTP client."""

    def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> Any:  # object with .status_code
        ...


class WebhookTransport:
    """Delivers serialized events to the central webhook endpoint."""

    def __init__(
        self,
        config: MonitorConfig,
        http_client: _HttpClient | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        import httpx

        self._config = config
        self._client = http_client or httpx.Client(timeout=config.timeout, follow_redirects=False)
        self._owns_client = http_client is None
        self._retry = retry_policy or RetryPolicy(max_attempts=config.max_retries + 1)
        self._log = get_logger(config.debug)

    # -- public API ----------------------------------------------------------

    def send(self, event: dict[str, Any]) -> bool:
        """POST *event* to the webhook with bounded retries.

        Returns ``True`` when the receiver answered 2xx. Never raises.
        """
        body = json.dumps(event).encode("utf-8")
        last_status: int | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                response = self._client.post(
                    self._config.webhook_url,
                    content=body,
                    headers=self.build_headers(event, body),
                    timeout=self._config.timeout,
                )
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    return True
                if not self._retry.should_retry(attempt, response.status_code):
                    break  # client error (4xx): retrying will not help
            except Exception as exc:  # noqa: BLE001 - transport must never raise
                last_status = None
                self._log.warning("ExceptionHub webhook attempt %d failed: %s", attempt, type(exc).__name__)
                if not self._retry.should_retry(attempt, None):
                    break
            if attempt < self._retry.max_attempts:
                self._retry.sleep_for(attempt)
        self._log.error(
            "ExceptionHub event %s delivery failed (last status: %s)",
            event.get("event_id", "?"),
            last_status if last_status is not None else "connection error",
        )
        return False

    def build_headers(self, event: dict[str, Any], body: bytes) -> dict[str, str]:
        """Auth, correlation and HMAC-SHA256 signing headers."""
        timestamp = str(int(time.time()))
        return {
            "Content-Type": "application/json",
            "X-Project-Name": self._config.project_name,
            "X-Webhook-Key": self._config.webhook_key,
            "X-Event-ID": str(event.get("event_id", "")),
            "X-Timestamp": timestamp,
            "X-Signature": self.hmac_sign(body, timestamp),
        }

    def hmac_sign(self, body: bytes, timestamp: str) -> str:
        """Compute the ``sha256=`` request signature.

        The signature covers ``timestamp + "." + body`` so the receiver can
        detect tampering and reject replays (it also bounds the accepted
        timestamp window).
        """
        message = timestamp.encode("utf-8") + b"." + body
        digest = hmac.new(self._config.webhook_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return "sha256=" + digest

    def close(self) -> None:
        """Close an owned HTTP client (called on shutdown)."""
        if self._owns_client and hasattr(self._client, "close"):
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass

"""Bounded exponential-backoff retry policy.

The policy is pure: it only computes *when* the next attempt happens, so it
is trivially testable. The transport decides whether to retry at all based
on the response (5xx / transport errors are retried; 4xx are not).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with jitter and a hard attempt limit.

    ``max_attempts`` counts the *total* number of tries (initial + retries),
    so the loop can never run forever.
    """

    max_attempts: int = 4  # 1 initial try + 3 retries by default
    initial_delay: float = 0.5
    factor: float = 2.0
    max_delay: float = 10.0
    jitter: float = 0.1  # +/- fraction of the delay

    def should_retry(self, attempt: int, response_status: int | None) -> bool:
        """Whether another attempt makes sense.

        ``response_status`` is ``None`` for transport-level failures
        (connection refused, timeout, etc.). 5xx and transport failures are
        retriable; any other HTTP status is not.
        """
        if attempt >= self.max_attempts:
            return False
        if response_status is None:
            return True
        if 500 <= response_status < 600:
            return True
        return False

    def delay_for(self, attempt: int) -> float:
        """Backoff delay *before* attempt ``attempt`` (1-based)."""
        base = min(self.initial_delay * (self.factor ** (attempt - 1)), self.max_delay)
        spread = base * self.jitter
        # random.uniform is fine here: jitter only needs to be unpredictable
        # to the server, not cryptographically secure.
        return base + random.uniform(-spread, spread)  # noqa: S311

    def sleep_for(self, attempt: int) -> None:
        """Blocking sleep used by the background worker between attempts."""
        time.sleep(self.delay_for(attempt))


def is_retriable_status(status: int | None) -> bool:
    return status is None or 500 <= status < 600

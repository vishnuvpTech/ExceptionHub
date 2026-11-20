"""Fingerprint generation for grouping similar events.

A fingerprint is a stable, deterministic string derived from the parts of an
event that identify *the same kind* of failure or operation. Two events with
the same fingerprint are considered the same "group".
"""

from __future__ import annotations

import hashlib


def _digest(*parts: object) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def exception_fingerprint(
    project: str,
    exception_type: str,
    function: str,
    line_number: int | None,
) -> str:
    """Group exceptions by (project, type, function, line).

    Using the deepest frame's function/line keeps two occurrences of the same
    bug together while separating genuinely different failure sites.
    """
    return _digest(project, "exc", exception_type, function, line_number)


def performance_fingerprint(project: str, operation: str) -> str:
    """Group slow-operation reports by (project, operation)."""
    return _digest(project, "perf", operation)


def message_fingerprint(project: str, message: str) -> str:
    """Group identical messages together (message truncated for stability)."""
    return _digest(project, "msg", (message or "")[:120])

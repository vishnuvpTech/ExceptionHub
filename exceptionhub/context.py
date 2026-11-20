"""Per-request / per-thread execution context for ExceptionHub.

The context is stored in :class:`contextvars.ContextVar`, so it is safe
across threads, asyncio tasks and greenlets: each concurrent request sees
only its own values. The same context var doubles as the recursive
reporting guard: while the SDK is inside one of its own code paths, further
capture calls become no-ops, which prevents a broken webhook transport from
triggering an infinite exception-reporting loop.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """Request-scoped context attached to events captured in this context."""

    request: dict[str, Any] | None = None
    request_id: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_event_fields(self) -> dict[str, Any]:
        """Return the fields to merge into an event built in this context."""
        fields: dict[str, Any] = {"tags": dict(self.tags)}
        if self.request:
            fields["request"] = self.request
        if self.request_id and self.request and "request_id" not in self.request:
            fields["request"]["request_id"] = self.request_id
        if self.extra:
            fields["extra"] = self.extra
        return fields


#: One context per task/thread. A fresh empty context is the default.
#: ``default=None`` (a mutable dataclass instance would be shared across all
#: tasks that never call ``set_context``) — :func:`get_context` materialises a
#: new empty context on demand.
_CONTEXT: ContextVar[ExecutionContext | None] = ContextVar("exceptionhub_context", default=None)

#: Set while the SDK itself is running, to suppress recursive reporting.
_IN_SDK: ContextVar[bool] = ContextVar("exceptionhub_in_sdk", default=False)


def get_context() -> ExecutionContext:
    """Return the current execution context (never ``None``)."""
    context = _CONTEXT.get()
    return context if context is not None else ExecutionContext()


def set_context(context: ExecutionContext) -> None:
    _CONTEXT.set(context)


def new_request_context(
    request: dict[str, Any] | None = None,
    request_id: str | None = None,
    tags: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> ExecutionContext:
    return ExecutionContext(request=request, request_id=request_id, tags=tags or {}, extra=extra or {})


def in_sdk() -> bool:
    """True while executing inside SDK code (recursion guard)."""
    return _IN_SDK.get()


class sdk_guard:
    """Context manager that marks the surrounding block as SDK-internal."""

    def __enter__(self) -> sdk_guard:
        self._token = _IN_SDK.set(True)
        return self

    def __exit__(self, *exc_info: object) -> None:
        _IN_SDK.reset(self._token)

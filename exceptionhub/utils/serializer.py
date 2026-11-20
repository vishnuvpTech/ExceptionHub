"""Event serialization helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:  # avoid a runtime circular import with the schemas package
    from exceptionhub.schemas.base import BaseEvent


def serialize_event(event: BaseEvent | dict[str, Any]) -> dict[str, Any]:
    """Convert an event model (or dict) into a plain JSON-ready dict.

    Nested Pydantic models are flattened recursively so the result is safe
    to hand to :func:`json.dumps` or :class:`httpx.Client.post`.
    """
    if isinstance(event, BaseModel):
        return event.model_dump(mode="json")
    if isinstance(event, dict):
        out: dict[str, Any] = {}
        for key, value in event.items():
            out[key] = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        return out
    raise TypeError(f"Cannot serialize {type(event)!r} as an event")

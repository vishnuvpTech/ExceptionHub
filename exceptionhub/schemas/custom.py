"""Pydantic schemas for message and custom application events."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from exceptionhub.schemas.base import BaseEvent


class MessageEvent(BaseEvent):
    """Manually reported log-style message."""

    event_type: str = "message"
    message: str
    model_config = {"extra": "allow"}


class CustomEvent(BaseEvent):
    """User-defined application event with a free-form payload.

    Future extensions (DB timing, memory, CPU, breadcrumbs, distributed
    traces) should be added as new concrete subclasses that inherit from
    this base, keeping the receiver-side schema extensible.
    """

    event_type: str = "custom"
    category: str = "custom"
    payload: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "allow"}

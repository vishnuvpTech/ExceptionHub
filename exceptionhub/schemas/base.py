"""Base Pydantic models shared by all ExceptionHub event schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from exceptionhub.utils.ids import new_event_id, utc_now_iso


class SystemInfo(BaseModel):
    """Static host/runtime information attached to every event."""

    hostname: str = Field(default="")
    python_version: str = Field(default="")
    os: str = Field(default="")


class ProjectInfo(BaseModel):
    """Project/environment metadata for an event."""

    name: str
    environment: str
    release: str


class BaseEvent(BaseModel):
    """Common fields for every event the SDK transmits."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(default_factory=new_event_id, description="Unique event identifier.")
    event_type: str = Field(description="One of: exception, performance, message, custom.")
    project: ProjectInfo
    timestamp: str = Field(default_factory=lambda: utc_now_iso(), description="UTC ISO-8601 timestamp.")
    severity: Literal["debug", "info", "warning", "error", "critical"] = "info"
    system: SystemInfo = Field(default_factory=SystemInfo)
    fingerprint: str | None = Field(default=None, description="Stable grouping key for similar events.")
    request: dict[str, Any] | None = Field(
        default=None, description="HTTP request context, if captured from a framework."
    )
    tags: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict, description="User-supplied context data.")

    @classmethod
    def utc_now(cls) -> datetime:
        return datetime.now(timezone.utc)


def now_iso() -> str:
    return utc_now_iso()

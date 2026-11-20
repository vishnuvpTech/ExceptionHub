"""Pydantic schema for performance events."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from exceptionhub.schemas.base import BaseEvent


class PerformanceDetail(BaseModel):
    """Timed operation result."""

    operation: str = Field(description="Name of the monitored operation.")
    execution_time: float = Field(description="Wall-clock execution time in seconds.")
    threshold: float = Field(description="Configured slow-operation threshold in seconds.")
    unit: str = "seconds"
    status: Literal["ok", "slow"] = Field(description="'slow' when execution_time >= threshold.")


class PerformanceEvent(BaseEvent):
    """Event payload for a (slow) operation timing report."""

    event_type: str = "performance"
    severity: str = "warning"
    performance: PerformanceDetail
    model_config = {"extra": "allow"}

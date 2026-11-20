"""Pydantic schema for exception events."""

from __future__ import annotations

from pydantic import BaseModel, Field

from exceptionhub.schemas.base import BaseEvent


class ExceptionDetail(BaseModel):
    """Structured details of a single captured exception."""

    type: str = Field(description="Exception class name, e.g. ZeroDivisionError.")
    message: str = Field(default="")
    filename: str = Field(default="")
    function: str = Field(default="")
    line_number: int | None = None
    stack_trace: str = Field(default="", description="Full traceback text.")
    module: str = Field(default="")

    @classmethod
    def from_exception(cls, exc: BaseException) -> ExceptionDetail:
        import traceback

        frame = None
        tb = exc.__traceback__
        if tb is not None:
            frame = tb.tb_frame if tb.tb_frame is not None else None
            # Walk to the deepest (most recent) frame of the exception.
            while tb.tb_next is not None:
                tb = tb.tb_next
                frame = tb.tb_frame

        filename = ""
        function = ""
        line_number: int | None = None
        module = ""
        if frame is not None:
            filename = frame.f_code.co_filename or ""
            function = frame.f_code.co_name or ""
            line_number = frame.f_lineno
            module = frame.f_globals.get("__name__", "") if frame.f_globals else ""

        stack_trace = "".join(traceback.format_exception(exc))
        return cls(
            type=type(exc).__name__,
            message=str(exc),
            filename=filename,
            function=function,
            line_number=line_number,
            stack_trace=stack_trace,
            module=module,
        )


class ExceptionEvent(BaseEvent):
    """Event payload describing an unhandled or manually captured exception."""

    event_type: str = "exception"
    severity: str = "error"
    exception: ExceptionDetail
    # The event schema is forward-compatible: frameworks can add fields.
    model_config = {"extra": "allow"}

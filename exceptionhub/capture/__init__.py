"""Event capture builders: exceptions, performance, and messages.

Each builder takes a config + the inputs and returns a fully populated event
dict ready for serialization and delivery. Builders never send events
themselves; the :class:`~exceptionhub.client.Monitor` wires them to the
worker.
"""

from exceptionhub.capture.exceptions import UnhandledExceptionHandler, build_exception_event, thread_unhandled_hook
from exceptionhub.capture.messages import build_custom_event, build_message_event
from exceptionhub.capture.performance import build_performance_event

__all__ = [
    "build_exception_event",
    "UnhandledExceptionHandler",
    "thread_unhandled_hook",
    "build_performance_event",
    "build_message_event",
    "build_custom_event",
]

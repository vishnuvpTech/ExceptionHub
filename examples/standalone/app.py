"""Standalone Python example.

Runs a small business function that deliberately fails and is slow, with
ExceptionHub reporting both to a local webhook receiver:

    # terminal 1: start the receiver
    python -m uvicorn main:app --port 8000   # from examples/webhook_receiver

    # terminal 2: run this example
    python examples/standalone/app.py
"""

from __future__ import annotations

import sys
import time

from exceptionhub import Monitor, monitor_performance, set_global_monitor

monitor = Monitor(
    project_name="resource-augmentation",
    webhook_url="http://127.0.0.1:8000/api/events/",
    webhook_key="PROJECT_WEBHOOK_SECRET",
    environment="development",
    release="1.0.0",
    capture_exceptions=True,
    capture_performance=True,
    performance_threshold=0.5,  # 500ms for the demo
)
monitor.initialize()
set_global_monitor(monitor)  # so bare @monitor_performance works below


def main() -> int:
    # 1. Manual message reporting.
    monitor.capture_message("Starting batch processing", level="info", tags={"batch": "demo"})

    # 2. Performance: a deliberately slow operation.
    with monitor.performance("batch_processing", threshold=0.5):
        time.sleep(0.8)

    # 3. Performance via decorator.
    @monitor_performance(operation="fast_op")
    def fast_op():
        time.sleep(0.1)
        return "done"

    fast_op()

    # 4. Manual exception reporting (caught, so the app does not crash).
    try:
        1 / 0
    except ZeroDivisionError:
        monitor.capture_exception()  # picks up the active exception

    # 5. Unhandled exception hook demonstration.
    try:
        monitor.capture_message("About to raise an unhandled error", level="warning")
        raise RuntimeError("intentional unhandled failure")
    except RuntimeError:
        # Simulate 'unhandled' by invoking the registered excepthook the
        # way the interpreter would for a top-level crash.
        import sys as _sys

        exc = sys.exc_info()[1]
        _sys.excepthook(RuntimeError, exc, exc.__traceback__)
        monitor.flush()
        monitor.capture_message("Unhandled error reported via excepthook", level="info")

    # Flush remaining events so the demo exits cleanly.
    monitor.flush()
    monitor.shutdown()
    print("ExceptionHub demo finished. Check the receiver at http://127.0.0.1:8000/api/events/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

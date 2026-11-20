"""Bounded background worker for non-blocking event delivery.

Events are pushed onto a fixed-size queue; a daemon thread drains it and
hands each event to the transport. When the queue is full the event is
dropped and logged (a monitoring SDK must never block or crash the host
application).
"""

from __future__ import annotations

import atexit
import queue
import threading
from typing import Any

from exceptionhub.logging import get_logger
from exceptionhub.transport.webhook import WebhookTransport

logger = get_logger()

_SHUTDOWN = object()  # sentinel that stops the worker loop


class EventWorker:
    """Threaded, bounded-queue event dispatcher."""

    def __init__(self, transport: WebhookTransport, max_queue_size: int = 1000, start: bool = True) -> None:
        self._transport = transport
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue_size)
        self._dropped = 0
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stopped = threading.Event()

        if start:
            self.start()
            atexit.register(self.shutdown)

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._worker is not None:
            return
        self._stopped.clear()
        self._worker = threading.Thread(target=self._run, name="exceptionhub-worker", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SHUTDOWN:
                    break
                try:
                    self._transport.send(item)
                except Exception:  # noqa: BLE001 - transport must never raise
                    logger.exception("ExceptionHub worker: unexpected error delivering event")
            finally:
                self._queue.task_done()
        self._stopped.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush queued events (FIFO, so they drain before the sentinel), then stop.

        Bounded by *timeout* seconds so application shutdown cannot hang.
        """
        if self._worker is None:
            self._transport.close()
            return
        try:
            self._queue.put_nowait(_SHUTDOWN)
        except queue.Full:  # pragma: no cover - queue nearly always has room
            pass
        if threading.current_thread() is not self._worker:
            self._worker.join(timeout=max(timeout, 0.5))
        self._worker = None
        self._stopped.set()
        self._transport.close()

    def flush(self, timeout: float = 5.0) -> int:
        """Block until the queue is empty or *timeout* expires.

        Returns the number of events still pending.
        """
        if self._worker is None:
            return 0
        # ``queue.Queue.join()`` waits until every submitted item has been
        # delivered (``task_done`` called by the worker) but has no timeout,
        # so run it in a helper thread and wait on it with one.
        drained = threading.Event()

        def _drain() -> None:
            self._queue.join()
            drained.set()

        watcher = threading.Thread(target=_drain, name="exceptionhub-flush", daemon=True)
        watcher.start()
        if drained.wait(timeout):
            return 0
        return self._queue.qsize()

    # -- event intake ---------------------------------------------------------

    def submit(self, event: dict[str, Any]) -> bool:
        """Queue *event* for delivery. Returns False (and logs) when dropped."""
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
            logger.warning(
                "ExceptionHub queue full - event %s dropped (total dropped: %d)",
                event.get("event_id", "?"),
                self._dropped,
            )
            return False

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def pending(self) -> int:
        return self._queue.qsize()

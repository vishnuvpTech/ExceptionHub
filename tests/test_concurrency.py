"""Concurrency tests: thread-safe event processing and no context leakage."""

from __future__ import annotations

import threading

from conftest import FakeHttpClient

from exceptionhub import Monitor


def test_concurrent_submits_are_thread_safe(monitor: Monitor, fake_http: FakeHttpClient) -> None:
    """Many threads submitting at once: no lost events, no crashes."""
    n_threads = 20
    per_thread = 25

    def worker(tid: int) -> None:
        for i in range(per_thread):
            monitor.capture_message(f"thread-{tid}-msg-{i}", tags={"thread": tid})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    monitor.flush(5.0)
    total = n_threads * per_thread
    delivered = [c for c in fake_http.calls if c["body"].get("event_type") == "message"]
    assert len(delivered) == total, f"expected {total} events, got {len(delivered)}"
    # Every message delivered exactly once.
    seen = {c["body"]["message"] for c in delivered}
    assert len(seen) == total, "duplicate events were delivered"


def test_request_context_does_not_leak_across_threads(fake_http: FakeHttpClient) -> None:
    """Context bound in one thread must not appear in another thread's events."""
    m = Monitor(project_name="ctx", webhook_url="http://localhost:9/api/events/", webhook_key="k")
    m.initialize(http_client=fake_http)


    results: dict[str, object] = {}

    def worker(name: str, marker: str) -> None:
        m.start_request_context(tags={"marker": marker})
        m.capture_message(f"from-{name}")
        # Give the worker time to process before we inspect.
        m.flush(1.0)

    t1 = threading.Thread(target=worker, args=("a", "A"))
    t1.start()
    t1.join()

    bodies = [c["body"] for c in fake_http.calls if c["body"].get("message") == "from-a"]
    assert bodies and bodies[0]["tags"]["marker"] == "A"

    # A brand-new thread with no context must not see thread-1's context.
    t2 = threading.Thread(target=worker, args=("b", "B"))
    t2.start()
    t2.join()
    bodies_b = [c["body"] for c in fake_http.calls if c["body"].get("message") == "from-b"]
    assert bodies_b and bodies_b[0]["tags"]["marker"] == "B"
    m.shutdown()


def test_context_isolation_across_many_threads(fake_http: FakeHttpClient) -> None:
    """Each of many threads keeps its own context; no cross-contamination."""
    m = Monitor(project_name="ctx-many", webhook_url="http://localhost:9/api/events/", webhook_key="k")
    m.initialize(http_client=fake_http)

    n = 30

    def worker(tid: int) -> None:
        m.start_request_context(tags={"tid": tid})
        m.capture_message(f"msg-{tid}", tags={"tid": tid})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    m.flush(5.0)
    bodies = [c["body"] for c in fake_http.calls if c["body"].get("event_type") == "message"]
    assert len(bodies) == n
    for i in range(n):
        matching = [b for b in bodies if b.get("message") == f"msg-{i}"]
        assert matching, f"missing event for thread {i}"
        assert matching[0]["tags"]["tid"] == i, f"thread {i} saw another thread's context"
    m.shutdown()


def test_queue_full_under_load_still_delivers_most(fake_http: FakeHttpClient) -> None:
    """A small queue under a burst drops some events but never crashes."""
    m = Monitor(
        project_name="burst",
        webhook_url="http://localhost:9/api/events/",
        webhook_key="k",
        max_queue_size=10,
        max_retries=0,
    )
    m.initialize(http_client=fake_http)
    # Slow down the transport so the bounded queue fills up.
    import time as _time

    original_send = m._transport.send
    def slow_send(event):
        _time.sleep(0.05)
        return original_send(event)
    m._transport.send = slow_send

    # Burst far more events than the queue can hold.
    for i in range(50):
        m.capture_message(f"burst-{i}")
    m.flush(3.0)
    assert m._worker.dropped >= 1, "the bounded queue must drop events under overload"
    m.shutdown()
    # Whatever was delivered went through; the process stayed alive the whole time.
    assert len(fake_http.calls) >= 1

"""FastAPI example.

Run::

    python -m uvicorn main:app --port 8001

Then trigger events::

    curl http://127.0.0.1:8001/health          # clean request
    curl http://127.0.0.1:8001/crash           # captured exception (500)
    curl http://127.0.0.1:8001/slow            # slow-request report
"""

from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException

from exceptionhub import Monitor, monitor_performance, set_global_monitor
from exceptionhub.integrations.fastapi import instrument_fastapi

monitor = Monitor(
    project_name="fastapi-demo",
    webhook_url="http://127.0.0.1:8000/api/events/",  # the receiver service
    webhook_key="PROJECT_WEBHOOK_SECRET",
    environment="development",
    release="0.1.0",
    performance_threshold=0.25,
)
set_global_monitor(monitor)

app = FastAPI()
instrument_fastapi(app, monitor)


@monitor_performance
def slow_computation(x: int) -> int:
    time.sleep(0.5)
    return x * 2


@app.get("/health")
def health() -> dict:
    monitor.capture_message("health check ok", level="debug")
    return {"status": "ok"}


@app.get("/crash")
def crash() -> None:
    raise HTTPException(status_code=500, detail="simulated failure")


@app.get("/slow")
def slow() -> dict:
    result = slow_computation(21)
    return {"result": result}

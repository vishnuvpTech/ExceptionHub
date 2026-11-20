"""Flask example.

Run::

    python app.py          # http://127.0.0.1:8002

Trigger events::

    curl http://127.0.0.1:8002/health
    curl http://127.0.0.1:8002/crash
    curl http://127.0.0.1:8002/slow
"""

from __future__ import annotations

import time

from flask import Flask

from exceptionhub import Monitor
from exceptionhub.integrations.flask import instrument_flask

monitor = Monitor(
    project_name="flask-demo",
    webhook_url="http://127.0.0.1:8000/api/events/",
    webhook_key="PROJECT_WEBHOOK_SECRET",
    environment="development",
    release="0.1.0",
    performance_threshold=0.25,
)
monitor.initialize()

app = Flask(__name__)
instrument_flask(app, monitor)


@app.route("/health")
def health() -> str:
    monitor.capture_message("health check ok", level="debug")
    return "ok"


@app.route("/crash")
def crash() -> None:
    raise RuntimeError("simulated failure")


@app.route("/slow")
def slow() -> str:
    time.sleep(0.5)
    return "done"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8002, debug=False)

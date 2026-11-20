"""Django example (self-contained; no migration or DB required).

Run::

    python run.py            # http://127.0.0.1:8003

Trigger events::

    curl http://127.0.0.1:8003/health
    curl http://127.0.0.1:8003/crash
    curl http://127.0.0.1:8003/slow

The example configures Django in-process (``settings.configure``) and
starts the dev server, so it needs no manage.py / project scaffolding.
"""

from __future__ import annotations

import sys
import time

import django
from django.conf import settings

# ---------------------------------------------------------------------------
# Configure Django in-memory (no settings module file needed for the example).
# ---------------------------------------------------------------------------
settings.configure(
    DEBUG=False,
    SECRET_KEY="django-example-not-for-production",
    ALLOWED_HOSTS=["testserver", "127.0.0.1", "localhost"],
    ROOT_URLCONF=__name__,
    INSTALLED_APPS=[
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "exceptionhub.integrations.django",  # wires the unhandled-exception signal
    ],
    DATABASES={},
    MIDDLEWARE=[
        "django.middleware.common.CommonMiddleware",
        "exceptionhub.integrations.django.ExceptionHubMiddleware",  # request timing
    ],
    EXCEPTIONHUB={
        "project_name": "django-demo",
        "webhook_url": "http://127.0.0.1:8000/api/events/",
        "webhook_key": "PROJECT_WEBHOOK_SECRET",
        "environment": "development",
        "release": "0.1.0",
        "performance_threshold": 0.25,
    },
)
django.setup()

from exceptionhub import Monitor  # noqa: E402

# Build the Monitor from the EXCEPTIONHUB settings block.
monitor = Monitor(**settings.EXCEPTIONHUB)
from exceptionhub.integrations.django import register_monitor  # noqa: E402

register_monitor(monitor)

from django.http import HttpResponse  # noqa: E402
from django.urls import path  # noqa: E402


def health(request) -> HttpResponse:
    monitor.capture_message("health check ok", level="debug")
    return HttpResponse("ok")


def crash(request) -> HttpResponse:
    raise ValueError("simulated failure")


def slow(request) -> HttpResponse:
    time.sleep(0.5)
    return HttpResponse("done")


urlpatterns = [
    path("health", health),
    path("crash", crash),
    path("slow", slow),
]


def main() -> int:
    from django.core.management import execute_from_command_line

    argv = [sys.argv[0], "runserver", "127.0.0.1:8003"]
    execute_from_command_line(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Host and runtime information collection."""

from __future__ import annotations

import platform
import socket


def get_system_info() -> dict:
    """Return hostname/Python/runtime facts for attaching to events.

    Returns a plain dict so this module can be imported without pulling in
    the Pydantic schemas (which would create an import cycle).
    """
    try:
        hostname = socket.gethostname()
    except OSError:  # pragma: no cover - extremely rare
        hostname = "unknown"
    return {
        "hostname": hostname,
        "python_version": platform.python_version(),
        "os": platform.system(),
    }

"""Internal SDK logging.

The SDK logs to the standard ``logging`` module under the ``exceptionhub``
logger. Internal logs never contain the webhook key or any other secret.
"""

from __future__ import annotations

import logging

LOGGER_NAME = "exceptionhub"


def get_logger(debug: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.NullHandler) for h in logger.handlers
    )
    # Only attach a default console handler when the app has not configured
    # any handler of its own; otherwise respect the application's setup.
    if not logger.handlers and not has_stream_handler:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
    return logger

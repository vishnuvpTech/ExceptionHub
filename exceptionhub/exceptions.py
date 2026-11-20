"""SDK-specific exceptions.

These are for *misconfiguration* of the SDK (bad config, invalid input to a
public API). They are distinct from the monitored application's own
exceptions, which the SDK must never swallow.
"""

from __future__ import annotations


class ExceptionHubError(Exception):
    """Base class for all ExceptionHub configuration/usage errors."""


class ConfigurationError(ExceptionHubError):
    """Raised when the SDK is initialized with an invalid configuration."""


class TransportError(ExceptionHubError):
    """Raised for fatal transport problems (only in synchronous flush)."""

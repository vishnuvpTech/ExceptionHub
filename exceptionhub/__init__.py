"""ExceptionHub: a lightweight, framework-agnostic Python monitoring SDK.

Install and use::

    from exceptionhub import Monitor

    monitor = Monitor(project_name="my-app", webhook_url="https://...")
    monitor.initialize()

    monitor.capture_message("something happened")
    monitor.capture_exception()          # inside an except: block
"""

from exceptionhub.client import Monitor, monitor_performance, set_global_monitor
from exceptionhub.config import MonitorConfig
from exceptionhub.context import ExecutionContext, new_request_context
from exceptionhub.exceptions import ConfigurationError, ExceptionHubError, TransportError
from exceptionhub.schemas.base import BaseEvent, ProjectInfo, SystemInfo
from exceptionhub.schemas.custom import CustomEvent, MessageEvent
from exceptionhub.schemas.exception import ExceptionDetail, ExceptionEvent
from exceptionhub.schemas.performance import PerformanceDetail, PerformanceEvent
from exceptionhub.utils.assets import icon_bytes, icon_path

__version__ = "0.1.0"

__all__ = [
    "Monitor",
    "MonitorConfig",
    "monitor_performance",
    "set_global_monitor",
    "ConfigurationError",
    "ExceptionHubError",
    "TransportError",
    "BaseEvent",
    "ProjectInfo",
    "SystemInfo",
    "ExceptionDetail",
    "ExceptionEvent",
    "PerformanceDetail",
    "PerformanceEvent",
    "MessageEvent",
    "CustomEvent",
    "ExecutionContext",
    "new_request_context",
    "icon_path",
    "icon_bytes",
    "__version__",
]

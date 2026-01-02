import asyncio
import datetime
import json
import logging
import os
import socket
import threading
from typing import Any, Dict, Union

def _json_default(obj: Any):
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    if hasattr(obj, 'model_dump'):
        try:
            return obj.model_dump()
        except Exception:
            return str(obj)
    return str(obj)


class StructuredLogFormatter(logging.Formatter):
    """Custom formatter for structured JSON logging."""

    def __init__(self):
        super().__init__()
        self.hostname = socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record into a structured JSON object."""
        # Base log entry
        log_entry = {
            "@timestamp": datetime.datetime.utcnow().isoformat(),
            "host": self.hostname,
            "level": record.levelname,
            "logger": record.name,
            "code": {
                "file": record.pathname,
                "function": record.funcName,
                "line": record.lineno
            },
            "metadata": {
                "version": "1.0.0",
                "environment": os.getenv("APP_ENV", "development"),
                "component": "tiptune",
                "thread_id": threading.get_ident(),
                "thread_name": threading.current_thread().name
            }
        }

        # Handle structured logging
        if isinstance(record.msg, dict):
            if "event" in record.msg:
                log_entry["event"] = record.msg["event"]
            if "data" in record.msg:
                log_entry["data"] = record.msg["data"]
            if "context" in record.msg:
                log_entry["context"] = record.msg["context"]
            if "error" in record.msg:
                log_entry["error"] = record.msg["error"]
        else:
            # Legacy string messages
            log_entry["message"] = record.getMessage()

        # Add exception info if present
        if record.exc_info:
            log_entry["error"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "stack_trace": self.formatException(record.exc_info)
            }

        # Add any extra attributes
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        # Add a newline character after the JSON to create spacing between log entries
        return json.dumps(log_entry, default=_json_default) + "\n"

class StructuredLogger:
    """Wrapper for standardized structured logging across the application."""

    def __init__(self, logger: Union[str, logging.Logger]):
        """Initialize with either a logger name or logger instance."""
        if isinstance(logger, str):
            self._logger = logging.getLogger(logger)
        else:
            self._logger = logger

    def _log(self, level: int, event_type: str,
             message: str = None,
             data: Dict[str, Any] = None,
             error: Dict[str, Any] = None,
             **extra: Any) -> None:
        """Internal method to create structured log entries."""
        log_entry = {
            "event_type": event_type,
            "data": data or {},
            **extra
        }

        if message:
            log_entry["message"] = message

        if error:
            log_entry["error"] = error

        self._logger.log(level, log_entry)

    def debug(self, event_type: str, message: str = None, **kwargs) -> None:
        """Log a debug message with structured data."""
        self._log(logging.DEBUG, event_type, message, **kwargs)

    def info(self, event_type: str, message: str = None, **kwargs) -> None:
        """Log an info message with structured data."""
        self._log(logging.INFO, event_type, message, **kwargs)

    def warning(self, event_type: str, message: str = None, **kwargs) -> None:
        """Log a warning message with structured data."""
        self._log(logging.WARNING, event_type, message, **kwargs)

    def error(self, event_type: str, message: str = None, **kwargs) -> None:
        """Log an error message with structured data."""
        self._log(logging.ERROR, event_type, message, **kwargs)

    def critical(self, event_type: str, message: str = None, **kwargs) -> None:
        """Log a critical message with structured data."""
        self._log(logging.CRITICAL, event_type, message, **kwargs)

    def exception(self, event_type: str, exc: Exception, message: str = None, **kwargs) -> None:
        """Log an exception with structured data."""
        error = {
            "type": type(exc).__name__,
            "message": str(exc)
        }
        self._log(logging.ERROR, event_type, message, error=error, **kwargs)

def get_structured_logger(name: str) -> StructuredLogger:
    """Get a structured logger instance."""
    return StructuredLogger(name)

async def cleanup_logging():
    """Cleanup function to properly close async logging handlers."""
    root_logger = logging.getLogger()

    # Close all handlers
    for handler in root_logger.handlers[:]:
        try:
            # Check if handler has async close method
            if hasattr(handler, 'close') and asyncio.iscoroutinefunction(handler.close):
                await handler.close()
            else:
                handler.close()
            root_logger.removeHandler(handler)
        except Exception as e:
            print(f"Error closing handler {handler}: {e}")

    # Small delay to allow final logs to be processed
    await asyncio.sleep(0.1)

"""CELL-19: Trace ID propagation and structured context."""
import contextvars
import uuid
import json
import os
import logging
from contextlib import contextmanager
from typing import Optional

# Global context for the current trace
current_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
current_group_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("group_id", default=None)

@contextmanager
def trace_context(trace_id: Optional[str] = None, group_id: Optional[int] = None):
    """Bind tracing metadata to the current async task and its children."""
    t_token = current_trace_id.set(trace_id or str(uuid.uuid4()))
    g_token = current_group_id.set(group_id)
    try:
        yield
    finally:
        current_trace_id.reset(t_token)
        current_group_id.reset(g_token)

def get_trace_id() -> Optional[str]:
    return current_trace_id.get()

def get_group_id() -> Optional[int]:
    return current_group_id.get()

class TraceLogFilter(logging.Filter):
    """Injects trace_id and group_id into log records."""
    def filter(self, record):
        record.trace_id = get_trace_id() or "-"
        record.group_id = get_group_id() or "-"
        return True


def setup_structured_logging(level=logging.INFO, log_file: Optional[str] = None):
    """Configure logging to include trace metadata and JSON formatting."""

    
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log_record = {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "trace_id": getattr(record, "trace_id", "-"),
                "group_id": getattr(record, "group_id", "-"),
            }
            if record.exc_info:
                log_record["exception"] = self.formatException(record.exc_info)
            return json.dumps(log_record, ensure_ascii=False)

    root = logging.getLogger()
    # Remove existing handlers
    for h in root.handlers[:]:
        root.removeHandler(h)

    # 1. Stdout handler
    stdout_handler = logging.StreamHandler()
    stdout_handler.addFilter(TraceLogFilter())
    stdout_handler.setFormatter(JsonFormatter())
    root.addHandler(stdout_handler)

    # 2. File handler (if provided)
    if log_file:

        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.addFilter(TraceLogFilter())
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)
    
    root.setLevel(level)


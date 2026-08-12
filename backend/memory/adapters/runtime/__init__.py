"""Runtime adapters exposing the Memory contracts."""

from .projection_safety import redact_projection_content, redact_projection_error
__all__ = [
           "redact_projection_content", "redact_projection_error"]

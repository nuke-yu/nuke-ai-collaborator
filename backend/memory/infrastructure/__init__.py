"""Infrastructure owned by the Memory bounded context."""

from .projection_outbox import DrainResult, ProjectionOutbox

__all__ = ["DrainResult", "ProjectionOutbox"]

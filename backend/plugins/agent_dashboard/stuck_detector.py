"""Compatibility exports for online_tasks.stuck_detector."""

from online_tasks.stuck_detector import (
    CHECK_INTERVAL_SEC,
    DEFAULT_MAX_AUTO_RETRIES,
    STUCK_TIMEOUT_SEC,
    StuckDetector,
)

__all__ = [
    "CHECK_INTERVAL_SEC",
    "DEFAULT_MAX_AUTO_RETRIES",
    "STUCK_TIMEOUT_SEC",
    "StuckDetector",
]

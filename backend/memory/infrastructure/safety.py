"""Safety policy for data entering canonical Memory storage."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from memory.domain.safety import redact_memory_secrets

MAX_MEMORY_TEXT = 4_000
MAX_MEMORY_JSON = 16_000
MAX_MEMORY_DEPTH = 6
MAX_MEMORY_ITEMS = 256


def safe_memory_text(value: Any, *, limit: int = MAX_MEMORY_TEXT) -> str:
    """Return bounded text with high-confidence secrets redacted."""
    text = str(value or "").strip()[: max(0, limit)]
    safe, _ = redact_memory_secrets(text)
    return safe[: max(0, limit)]


def safe_memory_mapping(value: Mapping[str, Any], *, limit: int = MAX_MEMORY_JSON) -> str:
    """Serialize a bounded, recursively sanitized JSON mapping."""
    bounded = _bound(value)
    raw = json.dumps(bounded, ensure_ascii=False, sort_keys=True, default=str)
    safe, _ = redact_memory_secrets(raw[: max(0, limit)])
    return safe[: max(0, limit)]


def _bound(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_MEMORY_DEPTH:
        return "[nested payload truncated]"
    if isinstance(value, Mapping):
        return {
            safe_memory_text(key, limit=200): _bound(item, depth + 1)
            for key, item in list(value.items())[:MAX_MEMORY_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [_bound(item, depth + 1) for item in list(value)[:MAX_MEMORY_ITEMS]]
    if isinstance(value, str):
        return safe_memory_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return safe_memory_text(value)

"""Pure standard-library safety primitives for canonical Memory values."""
from __future__ import annotations

import re
import json
from collections.abc import Mapping
from typing import Any

_REDACTED = "[REDACTED]"
_PATTERNS = (
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{36,}\b"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:xox[baprs])-([A-Za-z0-9-]{10,})\b"),
    re.compile(
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+)([A-Za-z0-9._-]{20,})"
    ),
)


def redact_memory_secrets(value: str) -> tuple[str, int]:
    """Redact high-confidence credentials without importing runtime layers."""
    total = 0
    text = value
    for pattern in _PATTERNS:
        text, count = pattern.subn(_REDACTED, text)
        total += count
    return text, total


MAX_MEMORY_TEXT = 4_000
MAX_MEMORY_JSON = 16_000
MAX_MEMORY_DEPTH = 6
MAX_MEMORY_ITEMS = 256
MAX_SAFETY_SCAN_CHARS = 65_536


def safe_memory_text(value: Any, *, limit: int = MAX_MEMORY_TEXT) -> str:
    # Redact before bounding: cutting a credential at the boundary can hide
    # its signature from the detector and persist a usable prefix.
    text = str(value or "").strip()
    if len(text) > MAX_SAFETY_SCAN_CHARS:
        tail = 4_096
        text = text[:MAX_SAFETY_SCAN_CHARS - tail] + "\n[...input bounded...]\n" + text[-tail:]
    safe, _ = redact_memory_secrets(text)
    return safe[: max(0, limit)]


def safe_memory_mapping(value: Mapping[str, Any], *, limit: int = MAX_MEMORY_JSON) -> str:
    budget = max(0, limit)
    if budget < 2:
        return ""
    bounded = _bound(value)
    safe, _ = redact_memory_secrets(json.dumps(bounded, ensure_ascii=False, sort_keys=True, default=str))
    while len(safe) > budget and isinstance(bounded, dict) and bounded:
        removable = next((key for key in reversed(bounded) if key != "_truncated"), None)
        if removable is None:
            break
        bounded.pop(removable)
        bounded["_truncated"] = True
        safe, _ = redact_memory_secrets(json.dumps(bounded, ensure_ascii=False, sort_keys=True, default=str))
    return safe if len(safe) <= budget else "{}"


def _bound(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_MEMORY_DEPTH:
        return "[nested payload truncated]"
    if isinstance(value, Mapping):
        return {safe_memory_text(key, limit=200): _bound(item, depth + 1)
                for key, item in list(value.items())[:MAX_MEMORY_ITEMS]}
    if isinstance(value, (list, tuple)):
        return [_bound(item, depth + 1) for item in list(value)[:MAX_MEMORY_ITEMS]]
    if isinstance(value, str):
        return safe_memory_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return safe_memory_text(value)

"""Pure standard-library safety primitives for canonical Memory values."""
from __future__ import annotations

import re

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

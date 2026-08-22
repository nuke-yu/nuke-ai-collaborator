"""Normalization of model-produced compaction summaries."""
from __future__ import annotations

import re


def format_compact_summary(raw: str) -> str:
    """Remove scratchpad analysis and return the structured summary body."""
    cleaned = re.sub(r"<analysis>.*?</analysis>", "", raw, flags=re.DOTALL).strip()
    match = re.search(r"<summary>(.*?)</summary>", cleaned, flags=re.DOTALL)
    return match.group(1).strip() if match else cleaned

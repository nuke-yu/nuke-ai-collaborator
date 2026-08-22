"""Token usage normalization for tool-loop AI responses."""
from __future__ import annotations


def accumulate_usage(target: list, result: dict) -> None:
    """Append normalized usage fields from one model response."""
    usage = result.get("usage") or {}
    if not usage:
        return
    target.append({
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_tokens", 0),
    })

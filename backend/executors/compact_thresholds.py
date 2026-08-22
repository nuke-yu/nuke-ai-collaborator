"""Context-window threshold calculations for compaction strategies."""
from __future__ import annotations


def autocompact_threshold(
    model_name: str,
    *,
    context_windows: dict[str, int],
    default_context_window: int,
    summary_output_tokens: int,
    buffer_tokens: int,
) -> int:
    window = context_windows.get(model_name, default_context_window)
    return window - summary_output_tokens - buffer_tokens


def snip_threshold(
    model_name: str,
    *,
    context_windows: dict[str, int],
    default_context_window: int,
) -> int:
    window = context_windows.get(model_name, default_context_window)
    return int(window * 0.70)

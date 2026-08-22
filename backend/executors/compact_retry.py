"""Bounded history reduction used when the compaction prompt overflows."""
from __future__ import annotations


def drop_oldest_rounds(messages: list[dict], fraction: float = 0.3) -> list[dict]:
    if len(messages) <= 1:
        return messages
    head = max(1, int(len(messages) * fraction))
    while head < len(messages) and messages[head].get("role") == "tool":
        head += 1
    if head >= len(messages):
        head = len(messages) - 1
    return messages[head:]

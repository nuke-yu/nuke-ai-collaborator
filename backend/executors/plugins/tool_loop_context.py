"""Pure message-history operations used by the tool loop.

This module deliberately has no runner, provider, or persistence dependencies.
Keeping message grouping here makes the provider protocol invariant testable
without importing the full executor stack.
"""
from __future__ import annotations

from typing import Any


def drop_oldest_message_group(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove one complete oldest turn while preserving tool-call pairing.

    System messages are retained. The removed group starts at the first
    non-system message and ends immediately before the next user message, so
    an assistant tool call and all following tool results are removed together.
    """
    if not messages:
        return messages
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        index += 1
    if index >= len(messages):
        return messages
    end = index + 1
    while end < len(messages) and messages[end].get("role") != "user":
        end += 1
    return messages[:index] + messages[end:]

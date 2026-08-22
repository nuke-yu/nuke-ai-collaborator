"""Rendering of conversation history for the compaction prompt."""
from __future__ import annotations


def render_history(messages: list[dict], clean_content) -> str:
    lines = []
    for message in messages:
        role = message.get("role", "")
        content = clean_content(message.get("content") or "")
        if role == "tool":
            lines.append(f"[工具结果 {message.get('name', '')}]: {content[:1000]}")
        else:
            lines.append(f"[{role}]: {content[:2000]}")
    return "\n".join(lines)

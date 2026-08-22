"""Completion-signal extraction from tool-loop transcripts."""
from __future__ import annotations

import json


def extract_completion_signals(
    messages: list[dict],
    tool_records: list[dict],
    execution_error: str | None = None,
) -> list[dict]:
    """Return workflow signals plus verified successful-tool evidence."""
    signals = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                name = func.get("name")
                if name not in ("signal_stage_done", "signal_rework"):
                    continue
                tc_id = tc.get("id")
                failed = False
                if tc_id:
                    for candidate in messages:
                        if candidate.get("role") == "tool" and candidate.get("tool_call_id") == tc_id:
                            content = str(candidate.get("content") or "")
                            if content.startswith("[") and "]" in content:
                                prefix = content.split("]", 1)[0] + "]"
                                failed = any(
                                    marker in prefix
                                    for marker in (
                                        "错误", "拒绝", "不存在", "受保护", "拦截", "异常",
                                        "fail", "error", "denied", "blocked",
                                    )
                                )
                            break
                if failed:
                    continue
                try:
                    args = (
                        json.loads(func.get("arguments") or "{}")
                        if isinstance(func.get("arguments"), str)
                        else func.get("arguments", {})
                    )
                except Exception:
                    args = {}
                signals.append({"name": name, "arguments": args})

    for record in tool_records:
        if not record.get("is_error", False):
            signals.append({
                "name": "_tool_succeeded",
                "arguments": {"tool_name": record.get("name", "")},
            })

    if execution_error:
        signals.append({
            "name": "_execution_failed",
            "arguments": {"reason": execution_error},
        })
    return signals

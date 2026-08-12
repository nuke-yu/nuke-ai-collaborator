from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executors.plugins.tool_loop_v1_helpers import _drop_oldest_message_group, _enforce_final_context_budget


def test_emergency_pruning_removes_tool_call_and_results_atomically():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "call-1"}], "content": ""},
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        {"role": "user", "content": "keep me"},
    ]
    assert _drop_oldest_message_group(messages) == [{"role": "user", "content": "keep me"}]


def test_final_context_budget_reduces_generation_when_window_is_exhausted() -> None:
    runner = SimpleNamespace(
        provider="deepseek",
        model_name="deepseek-chat",
        max_tokens=8192,
        system_prompt="system " * 30000,
        messages=[{"role": "user", "content": "message " * 30000}],
        tool_schemas=[
            {"type": "function", "function": {"name": "tool", "description": "x" * 3000}}
            for _ in range(100)
        ],
    )

    _enforce_final_context_budget(runner)

    assert runner.max_tokens < 8192
    assert runner.max_tokens >= 256

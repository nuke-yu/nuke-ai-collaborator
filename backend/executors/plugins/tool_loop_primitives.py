"""Small, dependency-light primitives shared by the tool-loop facade."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from core import config


class AiCall(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> Awaitable[dict]: ...


class ToolCall(Protocol):
    def __call__(self, name: str, arguments: dict, *, context: dict) -> Awaitable[str]: ...


async def tool_loop_core(
    system_prompt: str,
    messages: list,
    provider: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    tool_schemas: list,
    max_iter: int = 10,
    *,
    call_ai_once: AiCall,
    execute_tool_call: ToolCall,
) -> str:
    """Minimal tool-calling loop used by tests (no broadcaster or DB)."""
    messages = list(messages)
    history = []
    for _ in range(max_iter):
        result = await call_ai_once(
            system_prompt, messages, provider, model_name,
            temperature, max_tokens, tool_schemas,
        )
        if result["type"] != "tool_calls":
            history.clear()
            return result.get("content", "")
        serialized = json.dumps([
            {"name": call["name"], "arguments": call.get("arguments", {})}
            for call in result["calls"]
        ], sort_keys=True)
        history.append(serialized)
        if len(history) >= config.DOOM_LOOP_THRESHOLD:
            if all(item == serialized for item in history[-config.DOOM_LOOP_THRESHOLD:]):
                return f"[循环保护] 连续 {config.DOOM_LOOP_THRESHOLD} 次完全相同的工具调用，已终止循环"
        messages.append(result["assistant_message"])
        for call in result["calls"]:
            content = await execute_tool_call(
                call["name"], call.get("arguments", {}), context={}
            )
            messages.append({
                "role": "tool", "tool_call_id": call.get("id", ""),
                "name": call["name"], "content": content,
            })
    return "[达到最大工具调用次数，任务未完成]"


async def before_finalize_hook(draft, snap_messages, system_prompt, config_data,
                              provider, model_name, temperature, max_tokens,
                              ai_service, user_message):
    """Run the optional reviewer gate before finalizing a reply."""
    reviewer_prompt = config_data.get("reviewer_prompt", "")
    max_retries = int(config_data.get("max_retries", 2))
    cur_messages, cur_draft = list(snap_messages), draft
    for attempt in range(max_retries + 1):
        await ai_service.ctx.interaction.broadcast(ai_service.ctx.group_id, {
            "type": "before_finalize_review", "temp_id": ai_service.temp_id,
            "attempt": attempt + 1, "max_retries": max_retries + 1,
        })
        approved, feedback = True, ""
        try:
            review = await ai_service.call(
                reviewer_prompt,
                [{"role": "user", "content": f"【用户问题】\n{user_message}\n\n【待审查回复】\n{cur_draft}"}],
                model_name, provider, temperature, 512,
                auto_compact=False, operation="before_finalize_review",
            )
            feedback = review["content"] if review["type"] == "text" else ""
            approved = not feedback.strip().upper().startswith("REJECTED")
        except Exception:
            break
        if approved or attempt >= max_retries:
            await ai_service.ctx.interaction.broadcast(ai_service.ctx.group_id, {
                "type": "before_finalize_approved", "temp_id": ai_service.temp_id,
                "note": "" if approved else "retry budget exhausted",
            })
            break
        await ai_service.ctx.interaction.broadcast(ai_service.ctx.group_id, {
            "type": "before_finalize_rejected", "temp_id": ai_service.temp_id,
            "feedback": feedback[:300], "attempt": attempt + 1,
        })
        cur_messages += [
            {"role": "assistant", "content": cur_draft},
            {"role": "user", "content": f"[审查意见] {feedback}\n\n请根据以上意见修改回复。"},
        ]
        try:
            regen = await ai_service.call(
                system_prompt, cur_messages, model_name, provider, temperature, max_tokens,
                auto_compact=False, operation="before_finalize_regeneration",
            )
            if regen["type"] == "text":
                cur_draft = regen["content"]
        except Exception:
            break
    return cur_draft

"""Attenuated fork-skill execution loop."""
from __future__ import annotations

import permissions
from core import config


async def run_fork_skill(skill_content, task, provider, model, temperature, ai_service,
                         tool_schemas=None, *, parent_ruleset=None, spawn_depth=0,
                         group_id=None, bot_id=None, broadcaster=None, max_iter=8,
                         run_id=None, allowed_memory_refs=(), tool_records=None):
    if spawn_depth >= config.SPAWN_MAX_DEPTH:
        return f"[fork skill 已达最大深度 {config.SPAWN_MAX_DEPTH}，拒绝执行]"
    child_ctx = {"bot_id": bot_id, "group_id": group_id, "spawn_depth": spawn_depth + 1,
                 "ruleset": permissions.derive_subagent_ruleset(parent_ruleset),
                 "broadcaster": broadcaster, "run_id": run_id,
                 "allowed_memory_refs": allowed_memory_refs}
    messages = [{"role": "user", "content": task or "请执行此技能。"}]
    from executors.tool_dispatch import dispatch_tool
    for iteration in range(1, max_iter + 1):
        try:
            result = await ai_service.call(skill_content, messages, model, provider, temperature, 4096,
                                           tools=tool_schemas or None, auto_compact=True, operation="skill_fork")
        except Exception as exc:
            return f"[fork skill 执行错误] {exc}"
        if result["type"] == "text":
            return result["content"]
        if result["type"] != "tool_calls":
            return f"[fork skill 返回了非文本类型: {result['type']}]"
        calls = result.get("calls", [])
        if not tool_schemas:
            return f"[fork skill 请求工具 {', '.join(c['name'] for c in calls)} 但未声明 allowed_tools，已拒绝]"
        messages.append(result["assistant_message"])
        for call in calls:
            child_ctx["step_id"] = f"{run_id}:fork:{iteration}" if run_id else ""
            child_ctx["attempt_id"] = call.get("id", "")
            out, is_err = await dispatch_tool(call["name"], call.get("arguments", {}), child_ctx)
            if tool_records is not None:
                tool_records.append({"name": call["name"], "args": child_ctx.get("_executed_arguments", call.get("arguments", {})),
                                     "result": out, "is_error": is_err, "step_id": child_ctx["step_id"],
                                     "attempt_id": child_ctx["attempt_id"], "memory_refs": list(child_ctx.get("_validated_memory_refs", ())),
                                     "spawn_depth": spawn_depth + 1})
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": call["name"], "content": out})
    return "[fork skill 达到最大迭代次数，未完成]"

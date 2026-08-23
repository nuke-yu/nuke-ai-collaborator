"""Sub-agent spawning and workflow signal handlers for workspace tools."""
from __future__ import annotations

import asyncio
import uuid

import permissions


class NullBroadcaster:
    async def broadcast(self, group_id, message):
        pass


async def run_bg_agent(sub_ctx, bot_name: str, parent_steer, task_id: str, tasks: dict, registry) -> None:
    try:
        result = await registry.get(sub_ctx.bot.get("executor_id", "tool_loop_v1")).run(sub_ctx)
        reply = result.full_text or "[子 Agent 未返回内容]"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reply = f"[后台子Agent 执行错误] {exc}"
    finally:
        tasks.pop(task_id, None)
    if parent_steer is not None:
        await parent_steer.put(f"[后台子Agent「{bot_name}」已完成]\n{reply}")
    await sub_ctx.broadcaster.broadcast(sub_ctx.group_id, {"type": "bg_agent_done", "bot_name": bot_name, "preview": reply[:300]})


async def spawn_agent(bot_name: str, task: str, background: bool, context: dict, *, max_depth: int, execution_context, registry, tasks) -> str:
    ctx = context or {}
    all_bots, all_members = ctx.get("all_bots", []), ctx.get("all_members", [])
    depth = ctx.get("spawn_depth", 0)
    if depth >= max_depth:
        return f"[spawn_agent] 已达最大深度 {max_depth}，拒绝派生"
    target = next((bot for bot in all_bots if bot["name"] == bot_name), None)
    if not target:
        return f"[spawn_agent] 未找到 Bot「{bot_name}」。可用：{'、'.join(b['name'] for b in all_bots) or '（无）'}"
    sub_ctx = execution_context(
        bot=target, group_id=ctx.get("group_id"), user_message=task,
        sender={"id": 0, "name": "sub_agent", "type": "bot", "avatar_color": "#888"},
        history=[], all_bots=all_bots, all_members=all_members, interaction=ctx.get("interaction"),
        spawn_depth=depth + 1, ruleset=permissions.derive_subagent_ruleset(ctx.get("ruleset")),
    )
    if background:
        task_id = uuid.uuid4().hex
        tasks[task_id] = asyncio.create_task(run_bg_agent(sub_ctx, bot_name, ctx.get("steer_channel"), task_id, tasks, registry))
        return f"[后台子Agent 已启动] Bot「{bot_name}」正在后台执行，完成后结果将自动注回对话。task_id={task_id}"
    try:
        result = await registry.get(target.get("executor_id", "tool_loop_v1")).run(sub_ctx)
        return result.full_text or "[子 Agent 未返回内容]"
    except Exception as exc:
        return f"[spawn_agent 执行错误] {exc}"


async def signal_stage_done(reason: str, context: dict) -> str:
    runner = (context or {}).get("runner")
    if runner and bool((runner.bot.get("executor_config") or {}).get("require_pull_request_completion")):
        if not any(rec.get("name") == "create_pr" and not rec.get("is_error") for rec in runner.tool_records):
            return "[错误] 在调用 signal_stage_done 之前，必须先成功调用 create_pr 创建 Pull Request。请先调用 create_pr，确认成功后再调用 signal_stage_done。"
    return f"[系统] 已记录阶段完成信号。原因: {reason}。正在推进工作流..."


def signal_rework(target_stage: str, reason: str) -> str:
    return f"[系统] 已记录返工信号。目标阶段: {target_stage}，原因: {reason}。工作流即将打回..."

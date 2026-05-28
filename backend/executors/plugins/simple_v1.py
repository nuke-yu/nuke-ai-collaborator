import asyncio
import uuid

from executors.base import (
    BotExecutor, ExecutionContext, ExecutionResult,
    PluginManifest, WorkspaceConfig, CollabConfig, build_group_section,
)
from db import get_db, save_message, get_messages
from ai.client import call_ai_stream, AIError
from ai.memory import get_memory_context, add_to_chroma, maybe_summarize
from core.role_router import build_context_message
from workspace import append_log


def _with_personality(base_prompt: str, bot: dict) -> str:
    p = (bot.get("personality_prompt") or "").strip()
    return base_prompt + f"\n\n【性格指令】\n{p}" if p else base_prompt


class SimpleV1(BotExecutor):
    executor_id = "simple_v1"
    display_name = "基础对话"
    manifest = PluginManifest(
        description="单次 AI 调用，流式输出，自动记忆积累",
        tools=[],
        memory_layers=["short_term", "vector_search", "summary"],
        workspace=WorkspaceConfig(),
        collaboration=CollabConfig(can_handoff=True, can_spawn_subagent=False),
        max_iterations=1,
    )

    async def run(self, ctx: ExecutionContext) -> ExecutionResult:
        bot = ctx.bot
        history, user_msg = build_context_message(
            ctx.user_message, ctx.sender["name"], ctx.history
        )
        base = _with_personality(
            bot["system_prompt"] or f"你是{bot['name']}，{bot.get('role', '')}。",
            bot,
        )
        memory = await get_memory_context(bot["id"], bot.get("role") or "", ctx.user_message)
        group_section = build_group_section(ctx)
        system_prompt = (
            base
            + (f"\n\n{memory}" if memory else "")
            + (f"\n\n【群组信息】\n{group_section}" if group_section else "")
            + ctx.workflow_suffix
        )

        temp_id = str(uuid.uuid4())
        await ctx.broadcaster.broadcast(ctx.group_id, {
            "type": "stream_start", "temp_id": temp_id,
            "member_id": bot["id"], "sender_name": bot["name"],
            "sender_type": "bot", "avatar_color": bot["avatar_color"],
        })

        full_text = ""
        try:
            async for chunk in call_ai_stream(
                system_prompt, history, user_msg,
                bot.get("model_provider", "deepseek"),
                bot.get("model_name", "deepseek-chat"),
                bot.get("temperature", 0.7),
                bot.get("max_tokens", 4096),
            ):
                full_text += chunk
                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "stream_chunk", "temp_id": temp_id, "delta": chunk,
                })
        except AIError as e:
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "stream_error", "temp_id": temp_id, "message": str(e),
            })
            return ExecutionResult(full_text="", msg_id=None)

        # Sub-agents: close stream animation then return without DB/memory ops
        if ctx.spawn_depth > 0:
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "stream_end", "temp_id": temp_id, "id": None,
                "member_id": bot["id"], "sender_name": bot["name"],
                "preview": full_text[:100], "created_at": "",
            })
            return ExecutionResult(full_text=full_text, msg_id=None)

        async with get_db() as db:
            msg_id = await save_message(db, ctx.group_id, bot["id"], full_text)
            recent = await get_messages(db, ctx.group_id)

        await ctx.broadcaster.broadcast(ctx.group_id, {
            "type": "stream_end", "temp_id": temp_id, "id": msg_id,
            "member_id": bot["id"], "sender_name": bot["name"],
            "preview": full_text[:100],
            "created_at": recent[-1]["created_at"] if recent else "",
        })

        asyncio.create_task(add_to_chroma(msg_id, full_text, bot.get("role") or "", bot["id"]))
        asyncio.create_task(maybe_summarize(ctx.group_id, bot["id"], bot.get("role") or bot["name"], [bot["id"]]))
        asyncio.create_task(append_log(
            bot["id"], full_text,
            user_message=ctx.user_message,
            sender_name=ctx.sender.get("name", ""),
            executor=self.executor_id,
        ))

        return ExecutionResult(full_text=full_text, msg_id=msg_id)

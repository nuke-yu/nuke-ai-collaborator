"""
react_v1.py — ReAct (Reasoning + Acting) executor plugin.

Reasoning loop: Thought → Action → Observation → Thought → ... → Final Answer

Key difference from tool_loop_v1:
- Uses structured free-text prompting instead of native function calling.
- AI explicitly writes a Thought before each Action — visible reasoning chain.
- Works with any text model (no provider-side tool schema required).
- Higher default iteration limit (20) for complex multi-step tasks.
"""
import asyncio
import json
import re
import sys
import uuid

from executors.base import (
    BotExecutor, ExecutionContext, ExecutionResult,
    PluginManifest, WorkspaceConfig, CollabConfig, build_group_section,
)
from executors import tool_executor
from executors.plugins.workspace_tools import (
    _IS_WINDOWS, _WORKSPACE_TOOLS,
    _build_skills_xml, _with_personality,
    register_workspace_tools,
)
from db import get_db, save_message, get_messages
from ai.client import call_ai_once, AIError, AIContextOverflowError
from ai.memory import get_memory_context, add_to_chroma, maybe_summarize
from core.role_router import build_context_message
from workspace import load_context_files, format_context_blocks, append_log
from skills import list_skills_all, load_always_skills, filter_skills_by_context
import executors.compact as compact
from skills.constants import bot_ws as _bot_ws

_REACT_MAX_ITER = 20
_DOOM_LOOP_REPEAT = 3   # block if same action signature repeats N times


# ---------------------------------------------------------------------------
# System prompt — ReAct format instructions
# ---------------------------------------------------------------------------

_REACT_INSTRUCTIONS = """\
你使用 ReAct（Reasoning + Acting）框架工作。每轮回复只能是下面两种格式之一：

【格式 A — 需要工具时】
Thought: <分析当前情况，推理下一步应做什么>
Action: <工具名称>
Action Input: <工具参数，必须是合法 JSON>

【格式 B — 任务完成时】
Thought: <总结完成情况>
Final Answer: <直接对用户的最终回复>

---
可用工具：
{tools_section}

---
规则：
1. 每轮只能有一个 Action 或一个 Final Answer，不能同时出现两个。
2. Action Input 必须是合法 JSON，键名使用双引号。
3. 工具执行结果会以 Observation: 开头返回给你。
4. 不要重复执行完全相同的 Action（查看 Observation 历史再决策）。
5. 收集到足够信息后立即写 Final Answer，不要过度调用工具。
"""


def _build_tools_section(tool_defs: list, skills_xml: str) -> str:
    """Build a human-readable tools listing for the ReAct system prompt."""
    lines = []
    for t in tool_defs:
        # Extract required param names for a compact signature hint
        props = t.parameters.get("properties", {})
        required = t.parameters.get("required", list(props.keys()))
        sig = ", ".join(required)
        lines.append(f"- {t.name}({sig}): {t.description}")
    text = "\n".join(lines)
    if skills_xml:
        text += f"\n\n{skills_xml}\n使用 run_skill(name=\"技能名\") 调用"
    return text


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_react_response(text: str) -> dict:
    """Parse a ReAct-format AI response into a structured dict.

    Returns one of:
      {"type": "action", "thought": str, "tool": str, "input": dict}
      {"type": "final",  "thought": str, "answer": str}
      {"type": "raw",    "text": str}   — fallback when no pattern matches
    """
    thought_m = re.search(
        r"Thought:\s*(.*?)(?=\nAction:|\nFinal Answer:|$)", text, re.DOTALL
    )
    thought = thought_m.group(1).strip() if thought_m else ""

    final_m = re.search(r"Final Answer:\s*(.*)", text, re.DOTALL)
    if final_m:
        return {"type": "final", "thought": thought, "answer": final_m.group(1).strip()}

    action_m = re.search(r"Action:\s*(\S+)", text)
    input_m  = re.search(r"Action Input:\s*(\{.*\})", text, re.DOTALL)
    if action_m:
        tool_input: dict = {}
        if input_m:
            raw = input_m.group(1).strip()
            try:
                tool_input = json.loads(raw)
            except json.JSONDecodeError:
                # Trim trailing text after the closing brace and retry
                m2 = re.search(r"(\{[^{}]*\})", raw, re.DOTALL)
                if m2:
                    try:
                        tool_input = json.loads(m2.group(1))
                    except json.JSONDecodeError:
                        pass
        return {
            "type": "action",
            "thought": thought,
            "tool": action_m.group(1).strip(),
            "input": tool_input,
        }

    # No recognisable pattern — treat entire response as final answer
    return {"type": "raw", "text": text.strip()}


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class ReactV1(BotExecutor):
    executor_id = "react_v1"
    display_name = "ReAct 推理循环"

    def register_tools(self):
        register_workspace_tools()

    manifest = PluginManifest(
        description=(
            "ReAct 推理循环：每步先写 Thought 再执行 Action，形成可见的推理链。"
            "适合需要多步规划和自我纠错的复杂任务。"
        ),
        tools=_WORKSPACE_TOOLS,
        memory_layers=["short_term", "vector_search", "summary", "permanent"],
        workspace=WorkspaceConfig(
            startup_files=["AGENT.md", "BOOTSTRAP.md", "IDENTITY.md", "MEMORY.md"],
            skill_discovery=True,
            writeback_pattern="logs/{date}.md",
        ),
        collaboration=CollabConfig(can_handoff=True, can_spawn_subagent=True),
        max_iterations=_REACT_MAX_ITER,
    )

    async def run(self, ctx: ExecutionContext) -> ExecutionResult:  # noqa: C901
        bot = ctx.bot
        max_iter = (bot.get("executor_config") or {}).get("max_iterations", _REACT_MAX_ITER)
        model_name = bot.get("model_name", "deepseek-chat")
        provider   = bot.get("model_provider", "deepseek")
        temperature = bot.get("temperature", 0.7)
        max_tokens  = bot.get("max_tokens", 4096)

        history, user_msg = build_context_message(
            ctx.user_message, ctx.sender["name"], ctx.history
        )
        base = _with_personality(
            bot["system_prompt"] or f"你是{bot['name']}，{bot.get('role', '')}。", bot
        )
        memory = await get_memory_context(bot["id"], bot.get("role") or "", ctx.user_message)

        # Workspace context files
        context_blocks = await load_context_files(
            bot["id"], ctx.group_id, self.manifest.workspace.startup_files
        )
        context_text = format_context_blocks(context_blocks)

        # Skill discovery
        skills_xml = ""
        always_skills: list[dict] = []
        if self.manifest.workspace.skill_discovery:
            raw_skills = list_skills_all(
                bot["id"], group_id=ctx.group_id, role=bot.get("role")
            )
            lazy = [
                s for s in raw_skills
                if not s.get("always")
                and s.get("status", "active") != "disabled"
                and s.get("user_invocable", True)
            ]
            lazy = filter_skills_by_context(lazy, ctx.user_message)
            skills_xml, _ = _build_skills_xml(lazy, model_name)
            if any(
                s.get("always")
                for s in raw_skills
                if s.get("status", "active") != "disabled"
            ):
                always_skills = load_always_skills(
                    bot["id"], ctx.group_id, bot.get("role")
                )

        tools_section = _build_tools_section(self.manifest.tools, skills_xml)
        react_block   = _REACT_INSTRUCTIONS.format(tools_section=tools_section)

        always_section = ""
        if always_skills:
            parts = [f"=== {s['name']} ===\n{s['content']}" for s in always_skills]
            always_section = "\n\n【常驻技能 · 始终激活】\n" + "\n\n".join(parts)

        group_section = build_group_section(ctx)
        os_info = "Windows (PowerShell)" if _IS_WINDOWS else f"{sys.platform} (shell: /bin/sh)"
        system_prompt = (
            base
            + (f"\n\n{memory}" if memory else "")
            + (f"\n\n【群组信息】\n{group_section}" if group_section else "")
            + always_section
            + f"\n\n【运行环境】\nOS: {os_info}\n路径分隔符: {'\\\\' if _IS_WINDOWS else '/'}"
            + "\n\n" + react_block
            + ctx.workflow_suffix
        )

        context_prefix = f"【工作区文件】\n{context_text}\n\n" if context_text else ""
        prefixed_user  = (context_prefix + user_msg) if context_prefix else user_msg

        messages: list[dict] = list(history) + [
            {"role": "user", "content": prefixed_user}
        ]

        temp_id = str(uuid.uuid4())
        await ctx.broadcaster.broadcast(ctx.group_id, {
            "type": "stream_start", "temp_id": temp_id,
            "member_id": bot["id"], "sender_name": bot["name"],
            "sender_type": "bot", "avatar_color": bot["avatar_color"],
        })

        execution_ctx = {
            "bot_id":      bot["id"],
            "group_id":    ctx.group_id,
            "user_message": ctx.user_message,
            "all_bots":    ctx.all_bots,
            "all_members": ctx.all_members,
            "spawn_depth": ctx.spawn_depth,
            "broadcaster": ctx.broadcaster,
        }

        full_text   = ""
        iter_count  = 0
        tool_names_called: list[str] = []
        _action_counts: dict[str, int] = {}   # action_sig → repeat count

        def _build_reinject() -> str:
            return context_text

        try:
            while iter_count < max_iter:
                iter_count += 1

                # Overflow recovery (same pattern as tool_loop_v1)
                try:
                    result = await call_ai_once(
                        system_prompt, messages, provider, model_name,
                        temperature, max_tokens,
                        # No tool schemas — ReAct uses free-text, not function calling
                    )
                except AIContextOverflowError:
                    messages = await compact.compact_conversation(
                        messages, system_prompt, provider, model_name, temperature,
                        context_text=_build_reinject(),
                    )
                    await ctx.broadcaster.broadcast(ctx.group_id, {
                        "type": "compaction", "temp_id": temp_id,
                        "strategy": "overflow_recovery",
                        "message": "上下文溢出，已压缩后重试",
                    })
                    result = await call_ai_once(
                        system_prompt, messages, provider, model_name,
                        temperature, max_tokens,
                    )

                raw_text = result.get("content", "") if result["type"] == "text" else ""
                if not raw_text:
                    break

                parsed = _parse_react_response(raw_text)

                # Broadcast Thought
                if parsed.get("thought"):
                    await ctx.broadcaster.broadcast(ctx.group_id, {
                        "type": "react_thought", "temp_id": temp_id,
                        "member_id": bot["id"],
                        "thought": parsed["thought"],
                        "iteration": iter_count,
                    })

                # ── Final Answer ──────────────────────────────────────────
                if parsed["type"] in ("final", "raw"):
                    full_text = parsed.get("answer") or parsed.get("text", "")
                    # Fake-stream in small chunks for smooth UX
                    for i in range(0, len(full_text), 4):
                        await ctx.broadcaster.broadcast(ctx.group_id, {
                            "type": "stream_chunk", "temp_id": temp_id,
                            "delta": full_text[i:i + 4],
                        })
                    break

                # ── Action ────────────────────────────────────────────────
                tool_name  = parsed["tool"]
                tool_input = parsed["input"]
                action_sig = f"{tool_name}:{json.dumps(tool_input, sort_keys=True)}"

                # Doom-loop: same action repeated too many times
                _action_counts[action_sig] = _action_counts.get(action_sig, 0) + 1
                if _action_counts[action_sig] >= _DOOM_LOOP_REPEAT:
                    observation = (
                        f"[循环保护] 工具 {tool_name} 连续重复调用 {_DOOM_LOOP_REPEAT} 次，"
                        "请换一种方式解决问题或直接给出 Final Answer。"
                    )
                    messages.append({"role": "assistant", "content": raw_text})
                    messages.append({"role": "user", "content": f"Observation: {observation}"})
                    continue

                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "react_action", "temp_id": temp_id,
                    "member_id": bot["id"],
                    "tool": tool_name, "args": tool_input,
                    "iteration": iter_count,
                })

                try:
                    observation = await tool_executor.execute(
                        tool_name, tool_input, context=execution_ctx
                    )
                except Exception as exc:
                    observation = f"[工具执行错误] {exc}"

                tool_names_called.append(tool_name)

                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "react_observation", "temp_id": temp_id,
                    "member_id": bot["id"],
                    "tool": tool_name, "observation": observation[:300],
                    "iteration": iter_count,
                })

                # Append turn: assistant (thought+action) → user (observation)
                messages.append({"role": "assistant", "content": raw_text})
                messages.append({
                    "role": "user",
                    "content": f"Observation: {observation}",
                })

                # Lightweight compaction between rounds
                messages = compact.apply_tool_result_microcompact(messages)
                messages, _ = compact.snip_if_needed(messages, model_name)

            else:
                # Exhausted all iterations without Final Answer
                full_text = f"[已达最大推理步数 {max_iter}，任务未完成]"
                for i in range(0, len(full_text), 4):
                    await ctx.broadcaster.broadcast(ctx.group_id, {
                        "type": "stream_chunk", "temp_id": temp_id,
                        "delta": full_text[i:i + 4],
                    })

        except AIError as e:
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "stream_error", "temp_id": temp_id, "message": str(e),
            })
            return ExecutionResult(full_text="", msg_id=None)

        # Sub-agents return early — no DB, no memory, no broadcasts
        if ctx.spawn_depth > 0:
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

        asyncio.create_task(
            add_to_chroma(msg_id, full_text, bot.get("role") or "", bot["id"])
        )
        asyncio.create_task(
            maybe_summarize(
                ctx.group_id, bot["id"], bot.get("role") or bot["name"], [bot["id"]]
            )
        )
        asyncio.create_task(
            compact.maybe_compact_db_history(
                ctx.group_id, bot["id"], provider, model_name, temperature,
                ctx.broadcaster,
            )
        )
        asyncio.create_task(
            append_log(
                bot["id"], full_text,
                user_message=ctx.user_message,
                sender_name=ctx.sender.get("name", ""),
                tool_calls=tool_names_called,
                iterations=iter_count,
                executor=self.executor_id,
            )
        )

        return ExecutionResult(full_text=full_text, msg_id=msg_id)

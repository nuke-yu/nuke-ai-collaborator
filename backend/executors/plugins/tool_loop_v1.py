import asyncio
import uuid
import os
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"
_DEFAULT_SHELL = ["powershell.exe", "-NoProfile", "-Command"] if _IS_WINDOWS else ["/bin/sh", "-c"]

from executors.base import (
    BotExecutor, ExecutionContext, ExecutionResult,
    PluginManifest, ToolDef, WorkspaceConfig, CollabConfig, build_group_section,
)
from executors import tool_executor
from database import get_db, save_message, get_messages
from ai_client import call_ai_once, call_ai_stream_messages, AIError
from memory import get_memory_context, add_to_chroma, maybe_summarize
from role_router import build_context_message
from workspace import load_context_files, format_context_blocks, append_log, list_skills, load_always_skills

_WORKSPACE_TOOLS = [
    ToolDef(
        name="read_file",
        description="读取 Bot 工作区内的文件内容",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string", "description": "相对于工作区根目录的路径"}},
                    "required": ["path"]},
    ),
    ToolDef(
        name="write_file",
        description="向工作区文件写入内容（会覆盖）",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]},
    ),
    ToolDef(
        name="list_workspace",
        description="列出 Bot 工作区的目录结构",
        parameters={"type": "object", "properties": {}},
    ),
    ToolDef(
        name="run_skill",
        description="执行 skills/ 目录中的技能脚本",
        parameters={"type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "技能文件名（不含扩展名）"},
                        "args": {"type": "string", "default": ""},
                    },
                    "required": ["name"]},
    ),
    ToolDef(
        name="run_shell",
        description="在本地执行 shell 命令，返回 stdout / stderr / exit_code",
        parameters={"type": "object",
                    "properties": {
                        "cmd": {"type": "string", "description": "要执行的 shell 命令"},
                        "cwd": {"type": "string", "description": "工作目录（绝对路径），默认为用户 home 目录"},
                        "timeout": {"type": "integer", "description": "超时秒数，默认 30", "default": 30},
                    },
                    "required": ["cmd"]},
    ),
    ToolDef(
        name="read_local_file",
        description="读取本地任意路径的文件（工作区外）",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string", "description": "文件的绝对路径"}},
                    "required": ["path"]},
    ),
    ToolDef(
        name="write_local_file",
        description="写入本地任意路径的文件（自动创建父目录）",
        parameters={"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件的绝对路径"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"]},
    ),
]


def _with_personality(base_prompt: str, bot: dict) -> str:
    p = (bot.get("personality_prompt") or "").strip()
    return base_prompt + f"\n\n【性格指令】\n{p}" if p else base_prompt


class ToolLoopV1(BotExecutor):
    executor_id = "tool_loop_v1"
    display_name = "工具调用循环"

    def register_tools(self):
        import workspace as ws_module

        async def _read_file(path: str, context: dict = None) -> str:
            bot_id = (context or {}).get("bot_id")
            return await ws_module.read_file(bot_id, path) if bot_id else "[错误] 缺少 bot_id"

        async def _write_file(path: str, content: str, context: dict = None) -> str:
            bot_id = (context or {}).get("bot_id")
            return await ws_module.write_file(bot_id, path, content) if bot_id else "[错误] 缺少 bot_id"

        async def _list_workspace(context: dict = None) -> str:
            bot_id = (context or {}).get("bot_id")
            return await ws_module.list_workspace(bot_id) if bot_id else "[错误] 缺少 bot_id"

        async def _run_skill(name: str, args: str = "", context: dict = None) -> str:
            bot_id = (context or {}).get("bot_id")
            return await ws_module.run_skill(bot_id, name, args) if bot_id else "[错误] 缺少 bot_id"

        async def _run_shell(cmd: str, cwd: str = "", timeout: int = 30, context: dict = None) -> str:
            work_dir = cwd.strip() or str(Path.home())
            try:
                proc = await asyncio.create_subprocess_exec(
                    *_DEFAULT_SHELL, cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work_dir,
                    env={**os.environ},
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                out = stdout.decode(errors="replace").strip()
                err = stderr.decode(errors="replace").strip()
                parts = [f"exit_code: {proc.returncode}"]
                if out:
                    parts.append(f"stdout:\n{out}")
                if err:
                    parts.append(f"stderr:\n{err}")
                return "\n".join(parts)
            except asyncio.TimeoutError:
                return f"[超时] 命令执行超过 {timeout} 秒"
            except Exception as e:
                return f"[错误] {e}"

        async def _read_local_file(path: str, context: dict = None) -> str:
            try:
                return Path(path).read_text(encoding="utf-8")
            except FileNotFoundError:
                return f"[文件不存在] {path}"
            except Exception as e:
                return f"[读取错误] {e}"

        async def _write_local_file(path: str, content: str, context: dict = None) -> str:
            try:
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return f"已写入 {path}（{len(content)} 字符）"
            except Exception as e:
                return f"[写入错误] {e}"

        handlers = {
            "read_file": _read_file,
            "write_file": _write_file,
            "list_workspace": _list_workspace,
            "run_skill": _run_skill,
            "run_shell": _run_shell,
            "read_local_file": _read_local_file,
            "write_local_file": _write_local_file,
        }
        for tdef in _WORKSPACE_TOOLS:
            tool_executor.register(tdef, handlers[tdef.name])

    manifest = PluginManifest(
        description="多轮工具调用循环：AI 可读写工作区文件、执行技能，直至任务完成",
        tools=_WORKSPACE_TOOLS,
        memory_layers=["short_term", "vector_search", "summary", "permanent"],
        workspace=WorkspaceConfig(
            startup_files=["AGENT.md", "BOOTSTRAP.md", "IDENTITY.md"],
            skill_discovery=True,
            writeback_pattern="logs/{date}.md",
        ),
        collaboration=CollabConfig(can_handoff=True, can_spawn_subagent=False),
        max_iterations=10,
    )

    async def run(self, ctx: ExecutionContext) -> ExecutionResult:
        bot = ctx.bot
        max_iter = (bot.get("executor_config") or {}).get("max_iterations", self.manifest.max_iterations)

        history, user_msg = build_context_message(ctx.user_message, ctx.sender["name"], ctx.history)
        base = _with_personality(
            bot["system_prompt"] or f"你是{bot['name']}，{bot.get('role', '')}。", bot
        )
        memory = await get_memory_context(bot["id"], bot.get("role") or "", ctx.user_message)

        # Build context prefix injected as user message (not system prompt)
        context_blocks = await load_context_files(
            bot["id"], ctx.group_id, self.manifest.workspace.startup_files
        )
        context_text = format_context_blocks(context_blocks)

        lazy_skill_lines = []
        always_skill_blocks = []
        if self.manifest.workspace.skill_discovery:
            skills = list_skills(bot["id"])
            for s in skills:
                if s.get("always"):
                    always_skill_blocks.append(s["name"])
                else:
                    lazy_skill_lines.append(f"  - {s['name']}: {s['description']}")
            if always_skill_blocks:
                always_skills = load_always_skills(bot["id"])
            else:
                always_skills = []

        context_prefix = ""
        if context_text:
            context_prefix += f"【工作区文件】\n{context_text}\n\n"
        if lazy_skill_lines:
            skills_xml_parts = []
            for s in skills:
                if s.get("always"):
                    continue
                parts = [f"    <name>{s['name']}</name>",
                         f"    <description>{s['description']}</description>"]
                if s.get("when_to_use"):
                    parts.append(f"    <when_to_use>{s['when_to_use']}</when_to_use>")
                skills_xml_parts.append("  <skill>\n" + "\n".join(parts) + "\n  </skill>")
            skills_xml = "<available_skills>\n" + "\n".join(skills_xml_parts) + "\n</available_skills>"
            context_prefix += f"{skills_xml}\n使用 run_skill(name=\"技能名\") 调用\n\n"

        always_section = ""
        if always_skills:
            parts = [f"=== {s['name']} ===\n{s['content']}" for s in always_skills]
            always_section = "\n\n【常驻技能 · 始终激活】\n" + "\n\n".join(parts)

        group_section = build_group_section(ctx)
        os_info = f"Windows (PowerShell)" if _IS_WINDOWS else f"{sys.platform} (shell: /bin/sh)"
        system_prompt = (
            base
            + (f"\n\n{memory}" if memory else "")
            + (f"\n\n【群组信息】\n{group_section}" if group_section else "")
            + always_section
            + f"\n\n【运行环境】\nOS: {os_info}\n路径分隔符: {'\\\\' if _IS_WINDOWS else '/'}\n使用 run_shell 执行命令时请使用适合当前 OS 的语法。"
            + ctx.workflow_suffix
        )

        # Inject workspace context as prefix of the first user message
        prefixed_user_msg = (context_prefix + user_msg) if context_prefix else user_msg
        messages = list(history) + [{"role": "user", "content": prefixed_user_msg}]
        tool_names = [t.name for t in self.manifest.tools]
        tool_schemas = tool_executor.get_schemas(tool_names)

        temp_id = str(uuid.uuid4())
        await ctx.broadcaster.broadcast(ctx.group_id, {
            "type": "stream_start", "temp_id": temp_id,
            "member_id": bot["id"], "sender_name": bot["name"],
            "sender_type": "bot", "avatar_color": bot["avatar_color"],
        })

        provider = bot.get("model_provider", "deepseek")
        model_name = bot.get("model_name", "deepseek-chat")
        temperature = bot.get("temperature", 0.7)
        max_tokens = bot.get("max_tokens", 4096)
        full_text = ""

        async def _stream_final():
            nonlocal full_text
            async for chunk in call_ai_stream_messages(
                system_prompt, messages, provider, model_name, temperature, max_tokens
            ):
                full_text += chunk
                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "stream_chunk", "temp_id": temp_id, "delta": chunk,
                })

        try:
            if not tool_schemas:
                # No tools registered yet — pure streaming, same UX as simple_v1
                await _stream_final()
            else:
                execution_ctx = {"bot_id": bot["id"], "group_id": ctx.group_id}
                for _ in range(max_iter):
                    result = await call_ai_once(
                        system_prompt, messages, provider, model_name,
                        temperature, max_tokens, tool_schemas,
                    )
                    if result["type"] == "tool_calls":
                        messages.append(result["assistant_message"])
                        for call in result["calls"]:
                            await ctx.broadcaster.broadcast(ctx.group_id, {
                                "type": "tool_call", "temp_id": temp_id,
                                "tool": call["name"], "args": call["arguments"],
                            })
                            tool_result = await tool_executor.execute(
                                call["name"], call["arguments"], context=execution_ctx
                            )
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "name": call["name"],
                                "content": tool_result,
                            })
                            await ctx.broadcaster.broadcast(ctx.group_id, {
                                "type": "tool_result", "temp_id": temp_id,
                                "tool": call["name"], "result": tool_result[:300],
                            })
                    else:
                        # Tools resolved — stream the final answer properly
                        await _stream_final()
                        break
                if not full_text:
                    full_text = "[达到最大工具调用次数，任务未完成]"

        except AIError as e:
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "stream_error", "temp_id": temp_id, "message": str(e),
            })
            return ExecutionResult(full_text="", msg_id=None)

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
        asyncio.create_task(maybe_summarize(bot["id"], bot.get("role") or bot["name"], [bot["id"]]))
        asyncio.create_task(append_log(bot["id"], full_text))

        return ExecutionResult(full_text=full_text, msg_id=msg_id)

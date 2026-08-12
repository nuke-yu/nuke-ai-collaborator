import asyncio
import functools
import uuid
import json
import aiosqlite
from executors.base import (
    BotExecutor, ExecutionContext, ExecutionResult,
    PluginManifest, WorkspaceConfig, CollabConfig,
)
from core import config
from executors import tool_executor
from executors.plugins.workspace_tools import (
    _WORKSPACE_TOOLS,
    register_workspace_tools,
)
from executors.plugins.rd_tools import RD_TOOLS, register_rd_tools
from executors.plugins.memory_search_tool import MEMORY_TOOLS
import executors.compact as compact
from ai.client import call_ai_once, call_ai_stream_messages, AIError, AIContextOverflowError
from memory.canonical import build_conversation_memory_client, build_personal_knowledge_client, build_learning_client
from memory.domain import Principal
import workspace as _ws
from core.orchestration.ai_service import AIService
from ai.model_limits import resolve_max_tokens
from db import get_db
from workspace import load_context_files, format_context_blocks, append_log, archive_run
from skills import list_skills_all, load_always_skills, filter_skills_by_context
from core.role_router import build_context_message, build_image_content

async def _dispatch_tool(name: str, arguments: dict, context: dict) -> tuple[str, bool]:
    if not tool_executor.has_tool(name):
        from executors.tool_router import router as _tool_router
        if _tool_router.has_providers():
            return await _tool_router.execute(name, arguments, context=context)
    return await tool_executor.execute(name, arguments, context=context)

async def _execute_tool_call(name: str, arguments: dict, context: dict) -> str:
    res, _ = await _dispatch_tool(name, arguments, context)
    return res
from core.orchestration.prompt_builder import (
    filter_mcp_schemas as _filter_mcp_schemas,
    apply_external_schema_budget as _apply_external_schema_budget,
    build_budget_note as _build_budget_note,
    restrict_schemas as _restrict_schemas,
)

from executors.plugins.tool_loop_v1_helpers import (
    _acc_usage,
    _tool_loop_core,
    _before_finalize_hook,
    _run_fork_skill,
    setup_session,
    run_pre_compaction,
    poll_and_inject_signals,
    finalize_reply,
    cleanup_and_finalize,
    execute_parallel_tools,
    execute_serial_tools,
    generate_thinking_preview,
    get_fresh_context_prefix,
    build_reinject,
)

_DOOM_LOOP_THRESHOLD = config.DOOM_LOOP_THRESHOLD
_MAX_EXTERNAL_TOOL_SCHEMAS = 48


def _completion_deadline(
    schemas: list[dict],
    *,
    remaining_iterations: int,
    require_pull_request: bool,
    completion_signal_seen: bool,
) -> tuple[list[dict], str]:
    """Apply the Dashboard coding-task endgame policy."""
    if (
        not require_pull_request
        or completion_signal_seen
        or remaining_iterations > 8
    ):
        return schemas, ""

    suffix = (
        "\n\n[COMPLETION DEADLINE] "
        f"Only {remaining_iterations} model iterations remain. "
        "Stop adding features. Verify and finalize the current work now, "
        "call create_pr, then call signal_stage_done. If completion is "
        "impossible, call signal_rework instead."
    )
    if remaining_iterations > 3:
        return schemas, suffix

    completion_tools = {"create_pr", "signal_stage_done", "signal_rework"}
    return [
        schema for schema in schemas
        if schema["function"]["name"] in completion_tools
    ], suffix


class ToolLoopRunner:
    """Encapsulates the state and execution flow of a single AI tool loop run."""
    def __init__(self, executor, ctx: ExecutionContext):
        self.executor = executor
        self.ctx = ctx
        self.bot = ctx.bot
        self.max_iter = (self.bot.get("executor_config") or {}).get("max_iterations", executor.manifest.max_iterations)
        self.bf_config = (self.bot.get("executor_config") or {}).get("before_finalize")
        self.model_name = self.bot.get("model_name", "deepseek-chat")
        self.provider = self.bot.get("model_provider", "deepseek")
        self.memory = build_conversation_memory_client()
        self.learning = build_learning_client()
        personal_user_id = getattr(ctx, "personal_user_id", None)
        self.personal = (
            build_personal_knowledge_client(
                Principal.user(personal_user_id, [ctx.group_id])
            )
            if personal_user_id is not None and ctx.group_id is not None else None
        )
        self.temperature = self.bot.get("temperature", 0.7)
        self.max_tokens = resolve_max_tokens(self.provider, self.model_name, self.bot.get("max_tokens"))
        
        self.messages = []
        self.full_text = ""
        self.iter_count = 0
        self.consecutive_tool_only = 0
        self.tool_calls_history = []
        self.premature_text_count = 0
        self.completion_signal_seen = False
        self.tool_records = []
        self.execution_error: str | None = None
        self.file_tracker = {}
        self.invoked_skills = {}   # name -> inline skill body, for compaction survival
        self.temp_id = str(uuid.uuid4())
        self.session_id = ctx.resume_session_id or str(uuid.uuid4())
        self.run_id = self.session_id
        self.ai_service = AIService(ctx, self.session_id, self.temp_id)
        
        self.skills_xml = ""
        self.skills_snapshot = []
        self.always_skills = []
        self.system_prompt_base = ""
        self.system_prompt = ""
        self.tool_schemas = []

        self.rewake_queue = asyncio.Queue()
        self.execution_ctx = {}
        self.retrieved_experience_ids = []
        self.reflexion_used = False
        self.retrieved_skill_ids = []
        self.injected_memory_refs = ()
        self.memory_injection_decision_id = None

        self.ruleset = None
        self.use_cached_mc = compact.should_use_cached_microcompact(self.provider)

    async def _get_fresh_context_prefix(self) -> tuple[str, str]:
        return await get_fresh_context_prefix(self)

    async def _build_reinject(self) -> str:
        return await build_reinject(self)

    async def _finalize_reply(self):
        await finalize_reply(self)

    async def _setup_session(self):
        await setup_session(self)

    @staticmethod
    def _restrict_schemas(schemas: list, allowed: list | None) -> list:
        return _restrict_schemas(schemas, allowed)

    def _track_vfs_modifications(self, call_name: str, arguments: dict):
        _fpath = arguments.get("path", "")
        if _fpath:
            if call_name in compact._FILE_WRITE_TOOLS:
                self.file_tracker[_fpath] = "modified"
            elif call_name in compact._FILE_READ_TOOLS:
                self.file_tracker.setdefault(_fpath, "read")

    async def _handle_run_skill_result(self, tool_result: str) -> str:
        skill_max = self.execution_ctx.pop("skill_max_iterations", None)
        if skill_max and skill_max > self.max_iter:
            self.max_iter = skill_max
            
        skill_learns = self.execution_ctx.pop("skill_learns", None)
        if skill_learns:
            self.messages.append({
                "role": "user",
                "content": (
                    f"[系统] 技能「{skill_learns}」声明了 learns: true。"
                    f"请将本次执行的关键发现、规律或改进点总结为一个新技能，"
                    f"用 write_file 写入 `skills/learned/draft/{skill_learns}-learned.md`，"
                    f"使用 standard frontmatter（name/description/layer: learned/status: draft）。"
                ),
            })
            
        if tool_result == "__SKILL_FORK__":
            fork_info = self.execution_ctx.pop("skill_fork", {})
            fork_name = fork_info.get("name", "unknown")
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "skill_fork_start", "temp_id": self.temp_id,
                "member_id": self.bot["id"], "skill_name": fork_name,
            })
            fork_task = fork_info.get("args") or self.execution_ctx.get("user_message", "")
            fork_allowed = fork_info.get("allowed_tools", [])
            fork_schemas = (
                [s for s in self.tool_schemas if s["function"]["name"] in fork_allowed]
                if fork_allowed else None
            )
            fork_model = fork_info.get("model") or self.model_name
            child_sid = str(uuid.uuid4())
            await self.ctx.interaction.append_session_event(self.session_id, "child_fork", {
                "child_session_id": child_sid,
                "skill_name": fork_name,
            })
            
            tool_result = await _run_fork_skill(
                fork_info.get("content", ""),
                fork_task,
                self.provider, fork_model, self.temperature,
                self.ai_service,
                tool_schemas=fork_schemas,
                parent_ruleset=self.ruleset,
                spawn_depth=self.ctx.spawn_depth,
                group_id=self.ctx.group_id,
                bot_id=self.bot["id"],
                broadcaster=self.ctx.interaction,
                run_id=self.run_id,
                allowed_memory_refs=self.injected_memory_refs,
                tool_records=self.tool_records,
            )
            await self.ctx.interaction.append_session_event(self.session_id, "child_join", {
                "child_session_id": child_sid,
                "skill_name": fork_name,
                "result": tool_result,
            })
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "skill_fork_end", "temp_id": self.temp_id,
                "member_id": self.bot["id"], "skill_name": fork_name,
                "result": tool_result[:300],
            })
        return tool_result

    async def _execute_parallel_tools(self, calls, iteration=None):
        await execute_parallel_tools(self, calls, iteration)

    async def _execute_serial_tools(self, calls, iteration=None):
        await execute_serial_tools(self, calls, iteration)

    async def _run_pre_compaction(self):
        await run_pre_compaction(self)

    async def _poll_and_inject_signals(self):
        await poll_and_inject_signals(self)

    async def _cleanup_and_finalize(self) -> ExecutionResult:
        return await cleanup_and_finalize(self)

    async def _execute(self) -> ExecutionResult:
        """Execute the tool loop with thinking and progress output."""
        await self._setup_session()
        await self._run_pre_compaction()

        try:
            if not self.tool_schemas:
                await self._finalize_reply()
            else:
                self.execution_ctx = {
                    "bot_id": self.bot["id"],
                    "group_id": self.ctx.group_id,
                    "role": self.bot.get("role"),
                    "user_message": self.ctx.user_message,
                    "all_bots": self.ctx.all_bots,
                    "all_members": self.ctx.all_members,
                    "spawn_depth": self.ctx.spawn_depth,
                    "ruleset": self.ruleset,
                    "broadcaster": self.ctx.interaction,
                    "steer_channel": self.ctx.steer_channel,
                    "rewake_queue": self.rewake_queue,
                    "runner": self,
                    "run_id": self.run_id,
                    "allowed_memory_refs": self.injected_memory_refs,
                    "permission_event_recorder": functools.partial(
                        self.ctx.interaction.append_session_event,
                        self.session_id,
                    ),
                }

                _active_schemas = self.tool_schemas
                while self.iter_count < self.max_iter:
                    self.iter_count += 1
                    current_prefix, _ = await self._get_fresh_context_prefix()
                    self.system_prompt = self.system_prompt_base + (f"\n\n{current_prefix}" if current_prefix else "")

                    _skill_allowed = self.execution_ctx.pop("skill_allowed_tools", None)
                    if _skill_allowed is not None:
                        _active_schemas = [s for s in self.tool_schemas if s["function"]["name"] in _skill_allowed]
                    else:
                        _active_schemas = self.tool_schemas

                    require_pr_completion = bool(
                        (self.bot.get("executor_config") or {}).get(
                            "require_pull_request_completion"
                        )
                    )
                    remaining_iterations = self.max_iter - self.iter_count + 1
                    _active_schemas, deadline_suffix = _completion_deadline(
                        _active_schemas,
                        remaining_iterations=remaining_iterations,
                        require_pull_request=require_pr_completion,
                        completion_signal_seen=self.completion_signal_seen,
                    )
                    self.system_prompt += deadline_suffix

                    from skills.metadata import strip_context_window_suffix
                    _iter_model = strip_context_window_suffix(
                        self.execution_ctx.pop("skill_model", None) or self.model_name
                    )

                    await self.ctx.interaction.broadcast(self.ctx.group_id, {
                        "type": "ai_thought_start",
                        "temp_id": self.temp_id,
                        "iteration": self.iter_count,
                    })

                    thinking_draft = self._generate_thinking_preview(iter_count=self.iter_count)
                    chunk_size = 30
                    for i in range(0, len(thinking_draft), chunk_size):
                        await self.ctx.interaction.broadcast(self.ctx.group_id, {
                            "type": "ai_thought_delta",
                            "temp_id": self.temp_id,
                            "iteration": self.iter_count,
                            "delta": thinking_draft[i:i+chunk_size],
                        })
                        await asyncio.sleep(0.015)
                    await self.ctx.interaction.broadcast(self.ctx.group_id, {
                        "type": "ai_thought_end",
                        "temp_id": self.temp_id,
                        "iteration": self.iter_count,
                    })

                    try:
                        result = await self.ai_service.call(
                            self.system_prompt, self.messages, _iter_model, self.provider,
                            self.temperature, self.max_tokens, _active_schemas,
                            use_cached_microcompact=self.use_cached_mc,
                            reinject_fn=self._build_reinject,
                            operation="tool_loop_iteration",
                        )
                    except AIError as e:
                        self.execution_error = str(e)
                        await self.ctx.interaction.broadcast(self.ctx.group_id, {
                            "type": "stream_error", "temp_id": self.temp_id, "message": str(e),
                        })
                        break

                    if result["type"] == "text":
                        self.consecutive_tool_only = 0
                        self.tool_calls_history.clear()
                        self.full_text = result["content"]
                        require_signal = bool(
                            (self.bot.get("executor_config") or {}).get(
                                "require_tool_completion_signal"
                            )
                        )
                        if (
                            require_signal
                            and not self.completion_signal_seen
                            and self.premature_text_count < 2
                        ):
                            self.premature_text_count += 1
                            self.messages.append({
                                "role": "assistant",
                                "content": self.full_text,
                            })
                            self.messages.append({
                                "role": "user",
                                "content": (
                                    "[系统] 任务尚未结束。不要只描述下一步；请立即调用可用工具继续执行。"
                                    "只有完成全部实现与验证后才能调用 signal_stage_done，无法继续时调用 "
                                    "signal_rework。"
                                ),
                            })
                            await self.ctx.interaction.save_session_snapshot(
                                self.session_id, self.messages
                            )
                            self.full_text = ""
                            continue
                        await self._finalize_reply()
                        break

                    if result["type"] == "tool_calls":
                        def _serialize_calls(calls):
                            serialized = []
                            for c in calls:
                                serialized.append({
                                    "name": c["name"],
                                    "arguments": c.get("arguments", {})
                                })
                            return json.dumps(serialized, sort_keys=True)

                        current_serialized = _serialize_calls(result["calls"])
                        self.tool_calls_history.append(current_serialized)

                        if len(self.tool_calls_history) >= _DOOM_LOOP_THRESHOLD:
                            recent_history = self.tool_calls_history[-_DOOM_LOOP_THRESHOLD:]
                            if all(h == current_serialized for h in recent_history):
                                self.full_text = f"[循环保护] 连续 {_DOOM_LOOP_THRESHOLD} 次完全相同的工具调用，已终止循环"
                                break
                        self.messages.append(result["assistant_message"])
                        await self.ctx.interaction.save_session_snapshot(self.session_id, self.messages)

                        calls = result["calls"]
                        if any(
                            call.get("name") in {"signal_stage_done", "signal_rework"}
                            for call in calls
                        ):
                            self.completion_signal_seen = True
                        _run_parallel = (
                            len(calls) > 1
                            and all(tool_executor.is_concurrency_safe(c["name"]) for c in calls)
                        )
                        if _run_parallel:
                            await self._execute_parallel_tools(calls, iteration=self.iter_count)
                        else:
                            await self._execute_serial_tools(calls, iteration=self.iter_count)

                        from ai.reflexion import maybe_inject
                        try:
                            await maybe_inject(self, iteration=self.iter_count)
                        except aiosqlite.OperationalError:
                            pass

                        # Update completion_signal_seen based on actual successful execution
                        self.completion_signal_seen = any(
                            rec.get("name") in {"signal_stage_done", "signal_rework"} and not rec.get("is_error")
                            for rec in self.tool_records
                        )

                        self.messages = compact.apply_tool_result_microcompact(self.messages)
                        self.messages, _ = compact.snip_if_needed(self.messages, self.model_name)
                        self.messages, _ = await compact.auto_compact_if_needed(
                            self.messages, self.model_name, self.ctx.group_id,
                            self.system_prompt, self.provider, self.temperature,
                            self.ctx.interaction, self.temp_id, self.bot["id"],
                            context_text=await self._build_reinject(),
                            keep_recent=6,
                        )

                        await self._poll_and_inject_signals()
                    else:
                        self.consecutive_tool_only = 0
                        await self._finalize_reply()
                        break

                if not self.full_text:
                    self.full_text = "[达到最大工具调用次数，任务未完成]"

        except asyncio.CancelledError:
            async def _cleanup():
                try:
                    await self.ctx.interaction.update_session_status(self.session_id, "failed")
                    await self.ctx.interaction.broadcast(self.ctx.group_id, {
                        "type": "stream_aborted", "temp_id": self.temp_id, "member_id": self.bot["id"],
                        "session_id": self.session_id,
                    })
                    try:
                        from ai.execution_runs import finish_run
                        await finish_run(
                            run_id=self.run_id, group_id=self.ctx.group_id,
                            status="cancelled", iterations=self.iter_count,
                            input_tokens=self.ai_service.usage.input_tokens,
                            output_tokens=self.ai_service.usage.output_tokens,
                            error_summary="execution cancelled",
                        )
                    except aiosqlite.OperationalError:
                        pass
                except Exception:
                    pass
            # Note: asyncio.shield is a best-effort soft protection. In case of a second cancellation
            # (e.g. during a hard process shutdown), the shielded task can still be orphaned and
            # destroyed. This is acceptable for cleanups, but should not be relied upon for absolute,
            # crash-proof transaction guarantees.
            await asyncio.shield(_cleanup())
            raise
        except AIError as e:
            await self.ctx.interaction.update_session_status(self.session_id, "failed")
            from ai.execution_runs import finish_run
            await finish_run(
                run_id=self.run_id, group_id=self.ctx.group_id,
                status="failed", iterations=self.iter_count,
                input_tokens=self.ai_service.usage.input_tokens,
                output_tokens=self.ai_service.usage.output_tokens,
                error_summary=str(e),
            )
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "stream_error", "temp_id": self.temp_id, "message": str(e),
                "session_id": self.session_id,
            })
            return ExecutionResult(full_text="", msg_id=None, session_id=self.session_id)

        return await self._cleanup_and_finalize()

    async def execute(self) -> ExecutionResult:
        """Public entry point for execution."""
        return await self._execute()

    def _generate_thinking_preview(self, iter_count: int) -> str:
        return generate_thinking_preview(self, iter_count)


class ToolLoopV1(BotExecutor):
    executor_id = "tool_loop_v1"
    display_name = "工具调用循环"

    def register_tools(self):
        register_workspace_tools()
        register_rd_tools()

    manifest = PluginManifest(
        description="多轮工具调用循环：AI 可读写工作区文件、执行技能，直至任务完成",
        tools=_WORKSPACE_TOOLS + RD_TOOLS + MEMORY_TOOLS,
        memory_layers=["short_term", "vector_search", "summary", "permanent"],
        workspace=WorkspaceConfig(
            startup_files=["AGENT.md", "BOOTSTRAP.md", "IDENTITY.md", "MEMORY.md"],
            skill_discovery=True,
            writeback_pattern="logs/{date}.md",
        ),
        collaboration=CollabConfig(can_handoff=True, can_spawn_subagent=True),
        max_iterations=100,
    )

    async def run(self, ctx: ExecutionContext) -> ExecutionResult:
        if ctx.interaction is None:
            from core.orchestration.interaction import StandardInteraction
            ctx.interaction = StandardInteraction(ctx)
            
        runner = ToolLoopRunner(self, ctx)
        return await runner.execute()

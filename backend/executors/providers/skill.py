"""
providers/skill.py — SkillToolProvider

⚠️  DO NOT REGISTER THIS PROVIDER.  ⚠️
================================================================================
This is dormant Plan B scaffolding. It is intentionally NOT registered with the
ToolRouter (see runtime/entry.py — workers register only MCP providers + the
Builtin catch-all). It sits on NO execution path today.

execute() below calls skills.run_skill() DIRECTLY and therefore BYPASSES the
global before/after hooks — including the permission check
(_permission_check_hook) that normally gates run_skill in tool_executor.

The ToolRouter is FIRST-MATCH. If you register this provider, run_skill calls
will match here first and reach execute() WITHOUT any hook ever firing — a
fail-open permission regression (same class as the run_shell one fixed in
commit d5ab65e; see docs/TOOL-ROUTER-STRATEGIC-SOLUTION.md §四.3 / §八).

Currently run_skill stays in tool_executor's registry and is dispatched via
tool_executor.execute() (tool_loop_v1._dispatch_tool), so its hooks DO fire.
That guarantee holds ONLY because this provider is unregistered.

Before this provider may be registered, Plan B 阶段 1 must land first: hooks
must be lifted into ToolRouter.execute() as a non-bypassable pipeline. Until
then: leave it unregistered.
================================================================================
"""
import logging

from executors.base import ToolDef, ToolProvider

logger = logging.getLogger(__name__)

SKILL_TOOL = ToolDef(
    name="run_skill",
    description="执行 skills/ 目录中的技能脚本",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能文件名（不含扩展名）"},
            "args": {"type": "string", "default": ""},
        },
        "required": ["name"],
    },
)


class SkillToolProvider(ToolProvider):
    """
    Routes run_skill calls directly to skills.run_skill().

    ⚠️ DO NOT REGISTER — see module docstring. execute() bypasses the global
    permission hook; the ToolRouter is first-match, so registering this would
    let run_skill reach execute() with NO hook firing (permission regression).
    run_skill is correctly served via tool_executor today.
    """

    @property
    def provider_id(self) -> str:
        return "skill"

    def discover_tools(self) -> list[ToolDef]:
        return [SKILL_TOOL]

    def can_handle(self, name: str) -> bool:
        return name == "run_skill"

    async def execute(self, name: str, arguments: dict, context: dict) -> tuple[str, bool]:
        from skills import run_skill
        bot_id = context.get("bot_id")
        if not bot_id:
            return "[错误] 缺少 bot_id，无法执行 skill", True
        skill_name = arguments.get("name", "")
        args = arguments.get("args", "")
        try:
            result = await run_skill(bot_id, skill_name, args, ctx=context)
            result = str(result) if result is not None else "完成"
            is_error = result.startswith("[") and any(
                k in result for k in ("错误", "不存在", "失败", "fail", "error")
            )
            return result, is_error
        except Exception as e:
            logger.error(f"SkillProvider execute error [{skill_name}]: {e}")
            return f"[Skill执行错误] {e}", True

"""
providers/skill.py — SkillToolProvider

Owns the run_skill tool.  Skill execution goes through skills.run_skill()
directly — no tool_executor registration needed.

Why a dedicated provider:
  - Skills are declarative Markdown documents, not Python handlers.
  - Their lifecycle (discovery, argument substitution, markdown expansion)
    is managed by the skills/ subsystem, not by the tool_executor registry.
  - Separating them prevents skills from being accidentally affected by
    global hooks that target "all tools" (e.g., a future rate-limiter that
    shouldn't apply to local skill evaluation).

The global _permission_check_hook still fires for run_skill via the before-hook
pipeline (BuiltinToolProvider path), so permission gating is preserved.
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

    This provider does NOT call tool_executor.execute() — skill execution
    is handled entirely within the skills/ subsystem.
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

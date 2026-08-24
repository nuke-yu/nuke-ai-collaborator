"""Tool schema assembly for a tool-loop session."""
from __future__ import annotations

import logging

from core.orchestration import prompt_builder
from executors.tool_router import router

logger = logging.getLogger(__name__)


def assemble_tool_schemas(runner, *, skill_discovery: bool) -> None:
    """Build, filter, budget, and enrich the schemas visible to a session."""
    tool_names = [tool.name for tool in runner.executor.manifest.tools]
    if not skill_discovery:
        tool_names = [name for name in tool_names if name != "run_skill"]

    from executors import tool_executor

    if router.has_providers():
        builtin_schemas = tool_executor.get_schemas(tool_names)
        builtin_names = {item["function"]["name"] for item in builtin_schemas}
        mcp_schemas = [
            schema for schema in router.get_external_schemas()
            if schema["function"]["name"] not in builtin_names
        ]
        mcp_visibility = (runner.bot.get("executor_config") or {}).get("mcp") or {}
        mcp_schemas = prompt_builder.filter_mcp_schemas(
            mcp_schemas,
            mcp_visibility.get("allow"),
            mcp_visibility.get("block"),
        )
        mcp_schemas, deferred_names = prompt_builder.apply_external_schema_budget(mcp_schemas)
        if deferred_names:
            logger.warning(
                "tool schema budget: deferred %d MCP tool(s): %s",
                len(deferred_names), deferred_names,
            )
            budget_note = prompt_builder.build_budget_note(
                deferred_names, runner.ctx.group_id
            )
            runner.system_prompt_base += budget_note
            runner.system_prompt += budget_note
        runner.tool_schemas = builtin_schemas + mcp_schemas
    else:
        runner.tool_schemas = tool_executor.get_schemas(tool_names)

    runner.tool_schemas = prompt_builder.restrict_schemas(
        runner.tool_schemas, runner.bot.get("allowed_tools")
    )
    from runtime_features.code_mode import append_code_mode_prompt

    runner.system_prompt_base = append_code_mode_prompt(
        runner.system_prompt_base, runner.tool_schemas
    )
    runner.system_prompt = append_code_mode_prompt(
        runner.system_prompt, runner.tool_schemas
    )

    executor_config = runner.bot.get("executor_config") or {}
    allowed_tools = runner.bot.get("allowed_tools")
    memory_tools_allowed = not allowed_tools or all(
        name in allowed_tools for name in ("memory_read", "memory_write")
    )
    if (
        executor_config.get("memory_functions_enabled")
        and memory_tools_allowed
        and getattr(runner, "memory_functions", None) is not None
    ):
        runner.tool_schemas.extend(runner.memory_functions.tool_schemas())

    from memory.application.references import add_tool_ref_parameter

    runner.tool_schemas = add_tool_ref_parameter(
        runner.tool_schemas, runner.injected_memory_refs
    )

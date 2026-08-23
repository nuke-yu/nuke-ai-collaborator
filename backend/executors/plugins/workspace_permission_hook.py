"""Permission hook policy for workspace tools."""
from __future__ import annotations

import permissions

APPROVAL_REQUIRED_TOOLS = frozenset({"run_shell", "write_file", "read_local_file", "write_local_file", "spawn_agent", "run_code"})
AUTO_ALLOW_TOOLS = frozenset({"create_jira_ticket", "list_jira_tickets", "update_jira_ticket", "create_pr"})
READ_ONLY_CONFINED_TOOLS = frozenset({"list_workspace", "read_file", "read_anchored", "slice_read", "memory_search"})


async def permission_check(name: str, arguments: dict, context: dict, *, resolve_shell_cwd, is_destructive_git) -> dict | None:
    if name in AUTO_ALLOW_TOOLS:
        return None
    ruleset = context.get("ruleset")
    if ruleset is None:
        if name in APPROVAL_REQUIRED_TOOLS:
            return {"block": True, "reason": f"{name} 未接入权限系统（无 ruleset），出于安全已拒绝执行"}
        return None
    workspace_confined = name in READ_ONLY_CONFINED_TOOLS
    force_ask = False
    if name == "run_shell":
        cwd = (arguments.get("cwd") or "").strip()
        _, err = resolve_shell_cwd(cwd, context.get("bot_id"), context.get("group_id"))
        workspace_confined = err is None
        force_ask, _ = is_destructive_git((arguments.get("cmd") or "").strip())
    result = await permissions.check(
        tool_name=name, arguments=arguments, ruleset=ruleset,
        bot_id=context.get("bot_id"), broadcaster=context.get("broadcaster"),
        group_id=context.get("group_id"), spawn_depth=context.get("spawn_depth", 0),
        workspace_confined=workspace_confined, force_ask=force_ask,
        event_recorder=context.get("permission_event_recorder"),
    )
    if result["action"] == "deny":
        return {"block": True, "reason": result.get("reason", "权限拒绝")}
    persist_rule = result.get("persist_rule")
    if persist_rule is not None:
        ruleset.rules.append(persist_rule)
    return None

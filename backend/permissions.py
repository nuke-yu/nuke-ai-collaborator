"""
permissions.py — Rule model, decision pipeline, and ask-suspension mechanism.

Decision order per tool call:
  1. bypassPermissions mode → allow
  2. deny rules → deny
  3. allow rules → allow
  4. dontAsk mode → deny
  5. ask → suspend, broadcast permission_request, await user response
  6. default → allow (no matching rule)
"""
import asyncio
import fnmatch
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Rule:
    tool_pattern: str
    args_pattern: str = ""
    action: str = "allow"   # "allow" | "deny"
    id: int | None = None   # DB row id; None for in-memory once-rules


@dataclass
class Ruleset:
    rules: list[Rule] = field(default_factory=list)
    mode: str = "default"   # "default" | "bypassPermissions" | "dontAsk"


@dataclass
class _PendingRequest:
    future: "asyncio.Future[tuple[bool, str]]"
    bot_id: int
    tool_name: str
    arguments: dict


# In-memory "once" rules per bot_id (cleared on process restart)
_once_rules: dict[int, list[Rule]] = {}

# Pending ask futures keyed by request_id
_pending: dict[str, _PendingRequest] = {}


def _matches(rule: Rule, tool_name: str, arguments: dict) -> bool:
    if not fnmatch.fnmatch(tool_name, rule.tool_pattern):
        return False
    if rule.args_pattern:
        return any(
            fnmatch.fnmatch(str(v), rule.args_pattern)
            for v in arguments.values()
            if v is not None
        )
    return True


async def check(
    tool_name: str,
    arguments: dict,
    ruleset: Ruleset,
    bot_id: int,
    broadcaster: Any,
    group_id: int,
    spawn_depth: int = 0,
) -> dict:
    """
    Returns one of:
      {"action": "allow"}
      {"action": "deny",  "reason": str}
      {"action": "allow", "persist_rule": Rule}  — user approved with "always"
    """
    # 1. bypassPermissions → allow everything
    if ruleset.mode == "bypassPermissions":
        return {"action": "allow"}

    # Merge persistent (DB-loaded) rules with in-memory once-rules
    all_rules = list(ruleset.rules) + _once_rules.get(bot_id, [])

    # 2. deny rules take priority
    for rule in all_rules:
        if rule.action == "deny" and _matches(rule, tool_name, arguments):
            return {"action": "deny", "reason": f"规则拒绝: {rule.tool_pattern}"}

    # 3. allow rules
    for rule in all_rules:
        if rule.action == "allow" and _matches(rule, tool_name, arguments):
            return {"action": "allow"}

    # 4. dontAsk → deny without asking
    if ruleset.mode == "dontAsk":
        return {"action": "deny", "reason": "dontAsk 模式：未授权工具调用被拒绝"}

    # 5. Sub-agents can't show UI — treat unknown as deny
    if spawn_depth > 0:
        return {"action": "deny", "reason": "子 Agent 无法请求权限：工具未预授权"}

    # 6. ask — suspend execution and wait for user
    request_id = str(uuid.uuid4())
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending[request_id] = _PendingRequest(
        future=future, bot_id=bot_id, tool_name=tool_name, arguments=arguments,
    )

    await broadcaster.broadcast(group_id, {
        "type": "permission_request",
        "request_id": request_id,
        "tool": tool_name,
        "arguments": arguments,
    })

    try:
        approved, persistence = await future
    finally:
        _pending.pop(request_id, None)

    if not approved:
        return {"action": "deny", "reason": "用户拒绝授权"}

    new_rule = Rule(tool_pattern=tool_name, args_pattern="", action="allow")
    if persistence == "once":
        _once_rules.setdefault(bot_id, []).append(new_rule)
        return {"action": "allow"}
    # persistence == "always" → caller saves to DB
    return {"action": "allow", "persist_rule": new_rule}


def resolve(request_id: str, approved: bool, persistence: str = "once") -> "_PendingRequest | None":
    """
    Called from main.py when user responds to a permission_request.
    Returns the _PendingRequest so the caller can persist an "always" rule.
    Returns None if request_id is unknown or already resolved.
    """
    req = _pending.get(request_id)
    if not req or req.future.done():
        return None
    req.future.set_result((approved, persistence))
    return req

"""
Decision pipeline and ask-suspension mechanism.

Pipeline order per tool call:
  1. bypassPermissions mode → allow
  2. deny rules → deny
  3. allow rules → allow
  4. dontAsk mode → deny
  5. sub-agent (spawn_depth > 0) → deny (can't show UI)
  6. ask → suspend, broadcast permission_request, await user response
  7. default fallthrough → allow
"""
import asyncio
import fnmatch
import hashlib
import json
import uuid
from typing import Any

from .models import Rule, Ruleset, _PendingRequest


# Default-deny an unanswered ask after this many seconds. Backstops the case
# where the connection stays open but nobody responds (DFT-031): without it the
# tool-loop coroutine awaits forever and _pending grows unbounded.
_ASK_TIMEOUT_SECONDS = 300

# "once" grants scoped to (bot_id, group_id) → list of (tool_name, args_hash).
# Each grant auto-allows exactly ONE future identical call, then is consumed
# (DFT-032). Keying by group + the specific call stops "once" from leaking into
# other groups or becoming a permanent blanket allow.
_once_grants: dict[tuple[int, int], list[tuple[str, str]]] = {}

# Pending ask futures keyed by request_id
_pending: dict[str, _PendingRequest] = {}


def pending_stats() -> dict:
    """运行指标快照（DFT-057）：待审批 ask 队列堆积数 + 在册一次性放行票数。"""
    return {
        "pending_requests": len(_pending),
        "once_grants": sum(len(v) for v in _once_grants.values()),
    }


def _args_hash(arguments: dict) -> str:
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, default=str).encode()
    ).hexdigest()


def _matches(rule: Rule, tool_name: str, arguments: dict) -> bool:
    if not fnmatch.fnmatch(tool_name, rule.tool_pattern):
        return False
    if rule.args_pattern:
        # DFT-044: Recursive search in nested arguments to prevent bypass
        def search_nested(val: Any) -> bool:
            if isinstance(val, (str, int, float, bool)) or val is None:
                return fnmatch.fnmatch(str(val), rule.args_pattern)
            if isinstance(val, dict):
                return any(search_nested(v) for v in val.values())
            if isinstance(val, list):
                return any(search_nested(v) for v in val)
            return fnmatch.fnmatch(str(val), rule.args_pattern)

        return any(search_nested(v) for v in arguments.values())
    return True


def _consume_once_grant(bot_id: int, group_id: int, tool_name: str, arguments: dict) -> bool:
    """Return True (and remove the grant) iff a once-grant matches this exact call."""
    key = (bot_id, group_id)
    grants = _once_grants.get(key)
    if not grants:
        return False
    target = (tool_name, _args_hash(arguments))
    if target in grants:
        grants.remove(target)
        if not grants:
            _once_grants.pop(key, None)
        return True
    return False


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

    # 2. deny rules take priority
    for rule in ruleset.rules:
        if rule.action == "deny" and _matches(rule, tool_name, arguments):
            return {"action": "deny", "reason": f"规则拒绝: {rule.tool_pattern}"}

    # 3. persistent allow rules
    for rule in ruleset.rules:
        if rule.action == "allow" and _matches(rule, tool_name, arguments):
            return {"action": "allow"}

    # 4. once-grant for this exact (bot, group, tool, args) — consumed on use
    if _consume_once_grant(bot_id, group_id, tool_name, arguments):
        return {"action": "allow"}

    # 5. dontAsk → deny without prompting
    if ruleset.mode == "dontAsk":
        return {"action": "deny", "reason": "dontAsk 模式：未授权工具调用被拒绝"}

    # 6. Sub-agents can't show UI
    if spawn_depth > 0:
        return {"action": "deny", "reason": "子 Agent 无法请求权限：工具未预授权"}

    # 7. ask — suspend and wait for user response
    request_id = str(uuid.uuid4())
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending[request_id] = _PendingRequest(
        future=future, bot_id=bot_id, group_id=group_id,
        tool_name=tool_name, arguments=arguments,
    )

    await broadcaster.broadcast(group_id, {
        "type": "permission_request",
        "request_id": request_id,
        "tool": tool_name,
        "arguments": arguments,
    })

    try:
        approved, persistence = await asyncio.wait_for(future, _ASK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return {"action": "deny",
                "reason": f"权限请求超时（{_ASK_TIMEOUT_SECONDS}s 未响应），已自动拒绝"}
    finally:
        _pending.pop(request_id, None)

    if not approved:
        return {"action": "deny", "reason": "用户拒绝授权"}

    if persistence == "once":
        _once_grants.setdefault((bot_id, group_id), []).append(
            (tool_name, _args_hash(arguments))
        )
        return {"action": "allow"}
    # persistence == "always" → caller saves to DB
    return {"action": "allow",
            "persist_rule": Rule(tool_pattern=tool_name, args_pattern="", action="allow")}


def resolve(request_id: str, approved: bool, persistence: str = "once",
            group_id: int | None = None) -> "_PendingRequest | None":
    """
    Called from main.py when user responds to a permission_request.
    Returns the _PendingRequest so the caller can persist an "always" rule.
    Returns None if request_id is unknown or already resolved.

    DFT-031: when group_id is given it must match the request's group, so a
    client connected to one group cannot approve another group's tool call.
    """
    req = _pending.get(request_id)
    if not req or req.future.done():
        return None
    if group_id is not None and req.group_id != group_id:
        return None
    req.future.set_result((approved, persistence))
    return req


def cancel_pending_for_group(group_id: int) -> int:
    """Deny every still-pending ask for a group; returns how many were cancelled.

    DFT-031: called when the group's last client disconnects — nobody can answer
    the prompt anymore, so resolve the suspended tool calls as denied instead of
    leaving their coroutines awaiting forever. Resolving (rather than cancelling)
    the future keeps real task-abort CancelledError propagation intact (DFT-027).
    """
    n = 0
    for req in list(_pending.values()):
        if req.group_id == group_id and not req.future.done():
            req.future.set_result((False, "once"))
            n += 1
    return n

from core import config
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
import functools
import hashlib
import json
import uuid
from typing import Any

from .models import Rule, Ruleset, _PendingRequest
from .patterns import synthesize_args_pattern


# Default-deny an unanswered ask after this many seconds. Backstops the case
# where the connection stays open but nobody responds (DFT-031): without it the
# tool-loop coroutine awaits forever and _pending grows unbounded.
_ASK_TIMEOUT_SECONDS = config.ASK_TIMEOUT_SECONDS

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


@functools.lru_cache(maxsize=1024)
def _match_tool_pattern(tool_name: str, pattern: str) -> bool:
    return fnmatch.fnmatch(tool_name, pattern)


@functools.lru_cache(maxsize=512)
def _match_args_pattern(args_pattern: str, args_json: str) -> bool:
    """Cache args-pattern matching keyed by (args_pattern, serialised args).

    DFT-044: Recursive search in nested arguments to prevent bypass.
    Deserialises args_json to preserve correct nested-search semantics.
    """
    arguments = json.loads(args_json)

    def search_nested(val: Any) -> bool:
        if isinstance(val, (str, int, float, bool)) or val is None:
            return fnmatch.fnmatch(str(val), args_pattern)
        if isinstance(val, dict):
            return any(search_nested(v) for v in val.values())
        if isinstance(val, list):
            return any(search_nested(v) for v in val)
        return fnmatch.fnmatch(str(val), args_pattern)

    return any(search_nested(v) for v in arguments.values())


# Tools whose permission args_pattern matches a SINGLE identifying field, not a
# recursive search across every argument value. run_skill's `args` is freeform
# task text; recursive matching would let a name-scoped rule fire on the wrong
# skill (e.g. rule "deploy" firing on run_skill(name="build", args="deploy ...")).
_NAME_SCOPED_TOOLS = {"run_skill": "name"}


def _matches(rule: Rule, tool_name: str, arguments: dict) -> bool:
    if not _match_tool_pattern(tool_name, rule.tool_pattern):
        return False
    if rule.args_pattern:
        field = _NAME_SCOPED_TOOLS.get(tool_name)
        if field is not None:
            return fnmatch.fnmatch(str(arguments.get(field, "")), rule.args_pattern)
        args_json = json.dumps(arguments, sort_keys=True, default=str)
        return _match_args_pattern(rule.args_pattern, args_json)
    return True


# Sub-agent attenuation policy (blast-radius containment, not adversarial-code
# defense — this is a trusted internal tool). A spawned agent already cannot show
# a prompt (engine step 6 denies ask when spawn_depth>0), so it can only use
# rules it inherits. We further narrow what it inherits.
_SUBAGENT_NON_INHERITABLE_MODES = frozenset({"bypassPermissions"})
_SUBAGENT_HIGH_RISK_TOOLS = frozenset({
    "run_shell", "write_file", "write_local_file", "read_local_file", "spawn_agent",
})


def _covers_high_risk(tool_pattern: str) -> bool:
    if any(fnmatch.fnmatch(t, tool_pattern) for t in _SUBAGENT_HIGH_RISK_TOOLS):
        return True
    # MCP rules look like "mcp::server::tool" (or wildcards). A blanket allow for
    # a write-class MCP tool — or any MCP wildcard — is high-risk for a sub-agent
    # (e.g. mcp::filesystem::write_file). Lazy import avoids an import cycle.
    if tool_pattern.startswith("mcp::"):
        if "*" in tool_pattern:
            return True
        tool = tool_pattern.rsplit("::", 1)[-1]
        try:
            from executors.providers.mcp_client import _MCP_WRITE_TOOLS
        except Exception:
            return False
        return tool in _MCP_WRITE_TOOLS
    return False


def derive_subagent_ruleset(parent: "Ruleset | None") -> "Ruleset | None":
    """Attenuate a parent ruleset for a spawned sub-agent.

    - bypassPermissions does NOT propagate: a bypass parent must not hand its
      child unrestricted, recursive shell/file access. Child drops to 'default'
      (and since it can't prompt, that means only explicit allow rules pass).
    - All deny rules are kept (strict inheritance).
    - Blanket high-risk ALLOW rules (empty args_pattern covering run_shell/write/
      spawn, or a `*` allow) are dropped — a child must not inherit "allow ALL
      shell ops". Scoped allows (e.g. `git push *`) and low-risk allows are kept,
      so genuine pre-approvals still flow.
    """
    if parent is None:
        return None
    mode = "default" if parent.mode in _SUBAGENT_NON_INHERITABLE_MODES else parent.mode
    rules = [
        r for r in parent.rules
        if not (r.action == "allow" and (not r.args_pattern or r.args_pattern == "*") and _covers_high_risk(r.tool_pattern))
    ]
    return Ruleset(mode=mode, rules=rules)


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
    workspace_confined: bool = False,
    force_ask: bool = False,
) -> dict:
    """
    Returns one of:
      {"action": "allow"}
      {"action": "deny",  "reason": str}
      {"action": "allow", "persist_rule": Rule}  — user approved with "always"

    workspace_confined: when True the caller has already verified the tool
    operates entirely within the group workspace sandbox.  Deny rules still
    take priority; allow rules still override; but if nothing has decided by
    step 3.5 we skip the ask prompt and auto-allow.

    force_ask: when True this specific call must reach a human (e.g. destructive
    git, whose blast radius confinement cannot bound). It bypasses persistent
    allow rules (step 3) and the confined auto-allow (step 3.5), so no standing
    grant can silently pre-clear it. Deny rules (step 2) still win, an exact
    once-grant (step 4) is honored, and a sub-agent that cannot prompt is denied.
    """
    # 1. bypassPermissions → allow everything
    if ruleset.mode == "bypassPermissions":
        return {"action": "allow"}

    # 2. deny rules take priority
    for rule in ruleset.rules:
        if rule.action == "deny" and _matches(rule, tool_name, arguments):
            return {"action": "deny", "reason": f"规则拒绝: {rule.tool_pattern}"}

    # 3. persistent allow rules (skipped for force_ask: a standing/scoped grant —
    #    e.g. a `git reset *` synthesized from approving a benign `git reset` —
    #    must not silently pre-clear a destructive variant like `git reset --hard`).
    if not force_ask:
        for rule in ruleset.rules:
            if rule.action == "allow" and _matches(rule, tool_name, arguments):
                return {"action": "allow"}

    # 3.5. workspace-confined auto-allow: sandbox already restricts the blast
    # radius to the group workspace; no human prompt needed. force_ask opts out —
    # confinement does not bound a destructive-git blast radius.
    if workspace_confined and not force_ask:
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
    future: asyncio.Future = asyncio.get_running_loop().create_future()
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
    # persistence == "always" → caller saves to DB. Scope the rule to the *kind*
    # of call approved (e.g. `git push *`) instead of a blanket allow-all-args —
    # otherwise approving one shell command auto-allows every future one.
    return {"action": "allow",
            "persist_rule": Rule(
                tool_pattern=tool_name,
                args_pattern=synthesize_args_pattern(tool_name, arguments),
                action="allow",
            )}


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

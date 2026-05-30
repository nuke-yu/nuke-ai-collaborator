import fnmatch
import inspect
import re
from dataclasses import dataclass
from typing import Callable

from executors.base import ToolDef

_handlers: dict[str, Callable] = {}
_defs: dict[str, ToolDef] = {}


@dataclass
class _HookEntry:
    fn: Callable
    condition: str | None = None  # None = always run
    once: bool = False            # True = remove after first firing


_before_hooks: list[_HookEntry] = []
_after_hooks:  list[_HookEntry] = []


# ---------------------------------------------------------------------------
# Condition matching
# ---------------------------------------------------------------------------

def _condition_matches(condition: str, name: str, arguments: dict) -> bool:
    """Return True if the tool call satisfies the hook's condition filter.

    Syntax: "tool_pattern" or "tool_pattern(args_pattern)"

    Both parts use fnmatch glob syntax (* matches anything, ? matches one char).
    args_pattern is tested against each argument value individually; passes if
    ANY value matches (so "run_shell(git *)" matches cmd="git status").

    Malformed conditions default to True (fail open — hook runs).
    """
    m = re.match(r'^([^(]+)(?:\((.+)\))?$', condition.strip())
    if not m:
        return True

    name_pattern = m.group(1).strip()
    args_pattern = m.group(2)  # None when no () in condition

    if not fnmatch.fnmatch(name, name_pattern):
        return False

    if args_pattern:
        return any(
            fnmatch.fnmatch(str(v), args_pattern)
            for v in arguments.values()
            if v is not None
        )

    return True


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(tool_def: ToolDef, handler: Callable) -> None:
    _handlers[tool_def.name] = handler
    _defs[tool_def.name] = tool_def


def add_before_hook(hook: Callable, *, condition: str | None = None, once: bool = False) -> None:
    """Register a before-tool hook (idempotent per fn+condition pair).

    condition: optional fnmatch filter, e.g. "run_shell(git *)"
    once:      if True, the hook removes itself after firing once.

    Hook signature: async (name: str, arguments: dict, context: dict) -> dict | None
    Return {"block": True, "reason": "..."} to block execution; None to allow.
    """
    if not any(e.fn is hook and e.condition == condition for e in _before_hooks):
        _before_hooks.append(_HookEntry(fn=hook, condition=condition, once=once))


def add_after_hook(hook: Callable, *, condition: str | None = None, once: bool = False) -> None:
    """Register an after-tool hook (idempotent per fn+condition pair).

    condition:    optional fnmatch filter, e.g. "write_file(*.py)"
    once:         if True, the hook removes itself after firing once.

    Hook signature: async (name: str, arguments: dict, result: str, context: dict) -> str | None
    Return a new string to replace the result; None to leave it unchanged.
    Hooks run in registration order; each receives the (possibly transformed) result.
    asyncRewake:  hook may call `await context["rewake_queue"].put("message")` to inject
                  a [系统唤醒] message into the next AI round.
    """
    if not any(e.fn is hook and e.condition == condition for e in _after_hooks):
        _after_hooks.append(_HookEntry(fn=hook, condition=condition, once=once))


def clear_before_hooks() -> None:
    _before_hooks.clear()


def clear_after_hooks() -> None:
    _after_hooks.clear()


def _claim_once(hooks: list, entry: _HookEntry) -> bool:
    """Atomically claim a `once` hook before firing it (DFT-026).

    `in`-check + `remove` run with no await between them, so under concurrent
    execute() calls exactly one coroutine claims the entry — the rest skip it.
    Without this, two callers both fire the hook then both `.remove()` it, and
    the second raises ValueError(list.remove)."""
    if entry not in hooks:
        return False
    hooks.remove(entry)
    return True


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

async def execute(name: str, arguments: dict, context: dict | None = None) -> str:
    ctx = context or {}

    # Before hooks — first block wins; skipped when condition doesn't match.
    # DFT-026: iterate a snapshot (a concurrent execute() may mutate the global
    # list) and claim `once` hooks before firing so they fire exactly once.
    for entry in list(_before_hooks):
        if entry.condition and not _condition_matches(entry.condition, name, arguments):
            continue
        if entry.once and not _claim_once(_before_hooks, entry):
            continue
        try:
            verdict = await entry.fn(name, arguments, ctx)
            if verdict and verdict.get("block"):
                return f"[已拦截] {verdict.get('reason', '被安全策略拦截')}"
        except Exception as e:
            return f"[钩子错误] {e}"

    if name not in _handlers:
        return f"[错误] 工具 '{name}' 尚未实现"

    try:
        handler = _handlers[name]
        sig = inspect.signature(handler)
        if "context" in sig.parameters and context is not None:
            tool_result = await handler(**arguments, context=context)
        else:
            tool_result = await handler(**arguments)
        tool_result = str(tool_result) if tool_result is not None else "完成"
    except Exception as e:
        tool_result = f"[执行错误] {e}"

    # After hooks — each may transform the result; skipped when condition doesn't match.
    # DFT-026: snapshot iteration + claim `once` before firing (see before-hook note).
    for entry in list(_after_hooks):
        if entry.condition and not _condition_matches(entry.condition, name, arguments):
            continue
        if entry.once and not _claim_once(_after_hooks, entry):
            continue
        try:
            transformed = await entry.fn(name, arguments, tool_result, ctx)
            if transformed is not None:
                tool_result = transformed
        except Exception as e:
            tool_result += f"\n[after-hook 错误] {e}"

    return tool_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_concurrency_safe(name: str) -> bool:
    """Return True if the tool is read-only and safe to run in parallel."""
    td = _defs.get(name)
    return td.concurrency_safe if td else False


def get_schemas(names: list[str]) -> list[dict]:
    """Return OpenAI-format tool schemas for the given tool names."""
    result = []
    for name in names:
        if name in _defs:
            t = _defs[name]
            result.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            })
    return result

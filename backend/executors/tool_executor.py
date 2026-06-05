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

async def execute(name: str, arguments: dict, context: dict | None = None) -> tuple[str, bool]:
    """Execute a tool and return (result_string, is_error).
    
    Point 1: Accurate Error Tracking (DFT-034).
    """
    ctx = context or {}

    # Before hooks — first block wins; skipped when condition doesn't match.
    for entry in list(_before_hooks):
        if entry.condition and not _condition_matches(entry.condition, name, arguments):
            continue
        if entry.once and not _claim_once(_before_hooks, entry):
            continue
        try:
            verdict = await entry.fn(name, arguments, ctx)
            if verdict and verdict.get("block"):
                return f"[已拦截] {verdict.get('reason', '被安全策略拦截')}", True
        except Exception as e:
            return f"[钩子错误] {e}", True

    if name not in _handlers:
        return f"[错误] 工具 '{name}' 尚未实现", True

    is_error = False
    try:
        handler = _handlers[name]
        sig = inspect.signature(handler)
        
        is_truncated = arguments.pop("__truncated__", False)
        
        if "context" in sig.parameters and context is not None:
            tool_result = await handler(**arguments, context=context)
        else:
            tool_result = await handler(**arguments)
        tool_result = str(tool_result) if tool_result is not None else "完成"
        
        if is_truncated:
            hint = (
                f"\n\n[系统警告] 由于模型的输出长度限制，工具「{name}」的输入参数已被截断。\n"
                f"目前只执行了已被生成的前半部分（如果您刚才在写入代码/网页，部分内容已成功写入，但必然不完整）。\n"
                f"请不要尝试重新调用 write_file，请立刻分析当前代码，并使用 replace_file_content 增量追加和修补剩余被截断的内容！"
            )
            tool_result += hint
        
        # Point 1: Accurate Error Tracking (DFT-034).
        # Check if the output has a bracketed prefix containing error/block keywords.
        if tool_result.startswith("[") and "]" in tool_result:
            prefix = tool_result.split("]", 1)[0] + "]"
            if any(k in prefix for k in ("错误", "拒绝", "不存在", "受保护", "拦截", "异常", "fail", "error", "denied", "blocked")):
                is_error = True
            
    except Exception as e:
        tool_result = f"[执行错误] {e}"
        is_error = True

    # After hooks
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
            is_error = True

    return tool_result, is_error


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

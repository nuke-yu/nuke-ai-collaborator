import fnmatch
import inspect
import re
from dataclasses import dataclass
from typing import Callable

from executors.base import ToolDef

# Single registry: name -> ToolDef (with .handler bound).
# Replaces the two parallel dicts (_defs / _handlers) that used to exist.
_registry: dict[str, ToolDef] = {}

# LLMs frequently call file tools with a near-miss param name (`file_path`
# instead of the declared `path`, `contents` instead of `content`), which blows
# up the handler with "unexpected keyword argument". Normalize these well-known
# aliases onto the canonical param the handler actually accepts.
_ARG_ALIASES = {
    "file_path": "path",
    "filepath": "path",
    "filePath": "path",
    "contents": "content",
}


# JSON-schema type → acceptable Python type(s) for lightweight validation.
_JSON_TYPE_PY = {
    "string": (str,), "integer": (int,), "number": (int, float),
    "boolean": (bool,), "array": (list,), "object": (dict,),
}


def _validate_arguments(name: str, arguments: dict, parameters: dict | None) -> str | None:
    """Lightweight arg validation against the tool's JSON schema (#8).

    Catches the common LLM mistakes — missing required args and wrong scalar
    types — and returns a structured, model-readable error so the LLM can
    self-correct. Deliberately lenient: no additionalProperties / format checks
    (avoid false rejections); empty/absent schema → no-op.
    """
    if not isinstance(parameters, dict):
        return None
    props = parameters.get("properties")
    if not isinstance(props, dict):
        return None
    errors: list[str] = []
    for req in parameters.get("required", []) or []:
        if req not in arguments:
            errors.append(f"缺少必填参数 '{req}'")
    for key, val in arguments.items():
        spec = props.get(key)
        if not isinstance(spec, dict) or val is None:
            continue
        jtype = spec.get("type")
        py = _JSON_TYPE_PY.get(jtype)
        if not py:
            continue
        # bool is a subclass of int — don't let True/False satisfy integer/number.
        if jtype in ("integer", "number") and isinstance(val, bool):
            errors.append(f"参数 '{key}' 应为 {jtype}，收到 boolean")
        elif not isinstance(val, py):
            errors.append(f"参数 '{key}' 应为 {jtype}，收到 {type(val).__name__}")
    if errors:
        return f"[参数错误] {'；'.join(errors)}。请按工具 '{name}' 的参数 schema 修正后重试。"
    return None


def _normalize_arg_aliases(arguments: dict, sig: inspect.Signature) -> None:
    """In-place: rename known alias kwargs to the handler's canonical param.

    Only fires when the alias isn't itself a valid param and the canonical one
    is. The alias is always dropped (so it can't trigger an unexpected-kwarg
    error); the canonical value is only filled if the caller didn't already
    provide it explicitly.
    """
    params = sig.parameters
    for alias, canonical in _ARG_ALIASES.items():
        if alias in arguments and alias not in params and canonical in params:
            arguments.setdefault(canonical, arguments[alias])
            arguments.pop(alias)


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
    """Register a tool definition together with its handler.

    External callers (plugin register_tools() methods) keep the same
    signature — no plugin changes required.
    """
    tool_def.handler = handler
    _registry[tool_def.name] = tool_def


def has_tool(name: str) -> bool:
    """Public predicate: is `name` a builtin tool registered here?

    Used by callers to decide whether a tool is a local builtin (handle here,
    with the global before/after hooks) or something external like an MCP tool
    (route through ToolRouter instead)."""
    return name in _registry


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

    if name not in _registry:
        return f"[错误] 工具 '{name}' 尚未实现", True

    is_error = False
    try:
        entry = _registry[name]
        handler = entry.handler
        sig = inspect.signature(handler)
        
        is_truncated = arguments.pop("__truncated__", False)
        _normalize_arg_aliases(arguments, sig)

        validation_error = _validate_arguments(name, arguments, entry.parameters)
        if validation_error:
            tool_result = validation_error
            is_error = True
        elif is_truncated and name != "write_file":
            # Don't execute a truncated destructive call — but fall through so the
            # after-hooks below still run on this synthesized result.
            tool_result = (
                f"[安全拦截] 由于模型输出长度限制，工具「{name}」的参数被截断。为了防止操作执行不完整或损坏文件/环境，"
                f"系统已拦截并取消了此次执行。请重新规划，或尝试缩短范围/拆分成更小的操作重新调用。"
            )
            is_error = True
        else:
            if "context" in sig.parameters and context is not None:
                tool_result = await handler(**arguments, context=context)
            else:
                tool_result = await handler(**arguments)
            tool_result = str(tool_result) if tool_result is not None else "完成"

            if is_truncated:
                import editing
                tool_result += editing.build_completion_hint(
                    name, arguments.get("path", ""), len(arguments.get("content", "") or "")
                )

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
    td = _registry.get(name)
    return td.concurrency_safe if td else False


def get_schemas(names: list[str]) -> list[dict]:
    """Return OpenAI-format tool schemas for the given tool names."""
    result = []
    for name in names:
        if name in _registry:
            t = _registry[name]
            result.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            })
    return result

import inspect
from typing import Callable
from executors.base import ToolDef

_handlers: dict[str, Callable] = {}
_defs: dict[str, ToolDef] = {}
_before_hooks: list[Callable] = []
_after_hooks: list[Callable] = []


def register(tool_def: ToolDef, handler: Callable):
    _handlers[tool_def.name] = handler
    _defs[tool_def.name] = tool_def


def add_before_hook(hook: Callable):
    """Register a before-tool hook.

    Signature: async (name: str, arguments: dict, context: dict) -> dict | None
    Return {"block": True, "reason": "..."} to block execution.
    Return None to allow.
    """
    _before_hooks.append(hook)


def add_after_hook(hook: Callable):
    """Register an after-tool hook.

    Signature: async (name: str, arguments: dict, result: str, context: dict) -> str | None
    Return a new string to replace the result.
    Return None to leave the result unchanged.
    Hooks run in registration order; each receives the (possibly transformed) result.
    """
    _after_hooks.append(hook)


def clear_before_hooks():
    _before_hooks.clear()


def clear_after_hooks():
    _after_hooks.clear()


async def execute(name: str, arguments: dict, context: dict | None = None) -> str:
    ctx = context or {}

    # Before hooks — first block wins
    for hook in _before_hooks:
        try:
            verdict = await hook(name, arguments, ctx)
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

    # After hooks — each may transform the result
    for hook in _after_hooks:
        try:
            transformed = await hook(name, arguments, tool_result, ctx)
            if transformed is not None:
                tool_result = transformed
        except Exception as e:
            tool_result += f"\n[after-hook 错误] {e}"

    return tool_result


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

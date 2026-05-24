import inspect
from typing import Callable
from executors.base import ToolDef

_handlers: dict[str, Callable] = {}
_defs: dict[str, ToolDef] = {}


def register(tool_def: ToolDef, handler: Callable):
    _handlers[tool_def.name] = handler
    _defs[tool_def.name] = tool_def


async def execute(name: str, arguments: dict, context: dict | None = None) -> str:
    if name not in _handlers:
        return f"[错误] 工具 '{name}' 尚未实现"
    try:
        handler = _handlers[name]
        sig = inspect.signature(handler)
        if "context" in sig.parameters and context is not None:
            result = await handler(**arguments, context=context)
        else:
            result = await handler(**arguments)
        return str(result) if result is not None else "完成"
    except Exception as e:
        return f"[执行错误] {e}"


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

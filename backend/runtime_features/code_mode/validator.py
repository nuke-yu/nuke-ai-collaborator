from __future__ import annotations

import ast

from .domain import CodeModeRejected

ALLOWED_TOOLS = frozenset({"read", "write", "grep", "bash"})
ALLOWED_BUILTINS = {
    "bool": bool, "dict": dict, "enumerate": enumerate, "float": float,
    "int": int, "len": len, "list": list, "range": range, "sorted": sorted,
    "str": str, "tuple": tuple, "zip": zip,
}
BLOCKED_NAMES = frozenset({
    "__builtins__", "__import__", "eval", "exec", "compile", "open",
    "globals", "locals", "input", "breakpoint", "help", "quit", "exit",
})


def validate(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith,
                             ast.Try, ast.Raise, ast.Lambda, ast.While,
                             ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Global, ast.Nonlocal, ast.Delete,
                             ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            raise CodeModeRejected(f"禁止的 Code Mode 语法: {type(node).__name__}")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int) and abs(node.value) > 100_000:
                raise CodeModeRejected("数字字面量超过资源限制")
            if isinstance(node.value, str) and len(node.value) > 10_000:
                raise CodeModeRejected("字符串字面量超过资源限制")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, int) and node.right.value > 10_000:
                raise CodeModeRejected("重复展开规模超过资源限制")
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise CodeModeRejected(f"禁止访问名称: {node.id}")
        if isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name) or node.value.id != "tools":
                raise CodeModeRejected("只允许访问 tools SDK 方法")
            if node.attr not in ALLOWED_TOOLS:
                raise CodeModeRejected(f"禁止的 SDK 方法: {node.attr}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id not in ALLOWED_BUILTINS and node.func.id != "print":
                raise CodeModeRejected(f"禁止调用: {node.func.id}")
            if isinstance(node.func, ast.Attribute):
                if not isinstance(node.func.value, ast.Name) or node.func.value.id != "tools" or node.func.attr not in ALLOWED_TOOLS:
                    raise CodeModeRejected("禁止调用非 SDK 方法")
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise CodeModeRejected("禁止动态调用")

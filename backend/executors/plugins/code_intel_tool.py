"""`code_intel` builtin — semantic definition/references/hover/symbols.

Thin orchestration: confine the file to the group workspace, find the project
root for cross-file resolution, then dispatch to the engine the router selects
by language. The engine does the real work (Phase 1.5: jedi for Python).

Operation surface mirrors OpenCode's lsp tool (subset jedi serves well).
Coordinates are 1-based line + 1-based character, editor convention.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from typing import Optional

from executors.base import ToolDef, ToolResult
from executors.code_intel import router
# Reuse run_shell's confinement so code_intel obeys the group-workspace boundary.
# workspace_tools imports THIS module only lazily (in register_workspace_tools),
# so there is no import cycle.
from executors.plugins.workspace_tools import _resolve_shell_cwd


_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", ".git")
_POSITION_OPS = ("definition", "references", "hover")
_OPERATIONS = (*_POSITION_OPS, "document_symbols")


class CodeIntelParams(BaseModel):
    operation: str = Field(
        ..., description="definition | references | hover | document_symbols"
    )
    file: str = Field(..., description="目标文件，相对工作区根（如 workspace/<repo>/x.py）")
    line: Optional[int] = Field(
        None, description="1-based 行号（definition/references/hover 必填；document_symbols 不需要）"
    )
    character: Optional[int] = Field(
        None, description="1-based 列号（同上）"
    )


CODE_INTEL_TOOL_DEF = ToolDef(
    name="code_intel",
    description=(
        "代码语义解析（Python 用 jedi；JS/TS 用 typescript-language-server）。"
        "在 file 的 (line, character) 位置上：\n"
        "- definition: 跳到符号定义处\n"
        "- references: 找该符号的所有真实引用（跨文件、消歧，rename/重构前用，胜过文本匹配）\n"
        "- hover: 该符号的类型/签名/文档\n"
        "- document_symbols: 列出该文件的所有符号（不需 line/character）\n"
        "坐标 1-based（与编辑器一致）。位置通常先用 search 工具定位。其他语言请用 search。"
    ),
    parameters=CodeIntelParams,
    concurrency_safe=True,
)


def _project_root(abs_file: Path, ws_root: Path) -> Path:
    """Nearest dir with a project marker, walking up from the file, bounded by
    the group workspace root. Falls back to ws_root (broadest correct scope for
    cross-file references)."""
    for cur in [abs_file.parent, *abs_file.parent.parents]:
        if any((cur / m).exists() for m in _PROJECT_MARKERS):
            return cur
        if cur == ws_root:
            break
    return ws_root


def _rel(path_str: str, ws_root: Path) -> str:
    try:
        return str(Path(path_str).relative_to(ws_root))
    except (ValueError, TypeError):
        return path_str


def _format_locations(locs, ws_root: Path, header: str) -> str:
    if not locs:
        return f"未找到{header}"
    out = [f"{header}：{len(locs)} 处"]
    for loc in locs:
        snip = f"  {loc.snippet}" if loc.snippet else ""
        out.append(f"{_rel(loc.file, ws_root)}:{loc.line}:{loc.character}{snip}")
    return "\n".join(out)


async def _handle_code_intel(
    operation: str,
    file: str,
    line: Optional[int] = None,
    character: Optional[int] = None,
    context: dict = None,
) -> str:
    ctx = context or {}
    bot_id, group_id = ctx.get("bot_id"), ctx.get("group_id")

    if operation not in _OPERATIONS:
        return ToolResult.error(f"[参数错误] operation 必须是：{', '.join(_OPERATIONS)}")

    abs_file, err = _resolve_shell_cwd(file, bot_id, group_id)
    if err:
        return ToolResult.error(f"[安全拒绝] {err}")
    abs_file = Path(abs_file)
    if not abs_file.is_file():
        return ToolResult.error(f"[文件不存在] {file}")

    engine = router.get_engine(abs_file.suffix)
    if engine is None:
        if abs_file.suffix.lower() in router.supported_extensions():
            return ToolResult.error(
                f"[引擎未就绪] {abs_file.suffix} 的语义引擎不可用"
                "（JS/TS 需安装 typescript-language-server）。请用 search 工具按符号名检索。"
            )
        return ToolResult.error(
            f"[暂不支持] code_intel 不支持 {abs_file.suffix or '该文件类型'}。"
            f"支持：{', '.join(router.supported_extensions())}。其他类型用 search 工具。"
        )

    ws_root, _ = _resolve_shell_cwd("", bot_id, group_id)   # group workspace root
    ws_root = Path(ws_root)
    root = _project_root(abs_file, ws_root)

    if operation == "document_symbols":
        locs = await engine.document_symbols(abs_file, root)
        return _format_locations(locs, ws_root, header=f"{file} 的符号")

    if line is None or character is None:
        return ToolResult.error("[参数错误] definition/references/hover 需要 line 和 character（均 1-based）")

    if operation == "definition":
        locs = await engine.definition(abs_file, line, character, root)
        return _format_locations(locs, ws_root, header="定义")
    if operation == "references":
        locs = await engine.references(abs_file, line, character, root)
        return _format_locations(locs, ws_root, header="引用")
    # hover
    text = await engine.hover(abs_file, line, character, root)
    return text or "[无可用信息]"

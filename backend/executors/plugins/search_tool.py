"""`search` builtin — ripgrep content search, ported faithfully from OpenCode.

Mirrors OpenCode's grep tool (packages/opencode/src/tool/grep.ts + file/ripgrep.ts,
MIT):
  - rg flags: --no-config --json --hidden --glob=!.git/* --no-messages [--glob=<include>]
  - parses rg's --json stream into {path, line, text}
  - sorts matches by file mtime (most recently modified first)
  - groups output by file, caps at 100 matches, truncates lines at 2000 chars
  - same parameter surface: pattern / path / include (no case flag)

Adaptations for this codebase (NOT changing the rg behavior):
  - the search root is confined to the group workspace via _resolve_shell_cwd
    (group isolation — OpenCode's external-directory check is our group boundary)
  - secrets in results are masked by the global _default_secret_redactor after-hook
  - requires rg present (OpenCode bundles/downloads it); we error clearly if absent
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from pydantic import BaseModel, Field
from typing import Optional

from executors.base import ToolDef
# Reuse run_shell's confinement + secret-stripped env so search obeys the same
# group-workspace boundary. workspace_tools imports THIS module only lazily
# (inside register_workspace_tools), so there is no import cycle.
from executors.plugins.workspace_tools import _resolve_shell_cwd, _sandbox_env


_MAX_LINE_LENGTH = 2000       # OpenCode MAX_LINE_LENGTH
_RESULT_LIMIT = 100           # OpenCode `limit`
_SEARCH_TIMEOUT_S = 30


class SearchParams(BaseModel):
    pattern: str = Field(..., description="The regex pattern to search for in file contents")
    path: Optional[str] = Field(
        None,
        description="搜索目录，相对工作区（默认=群组共享工作区根）；可缩小到子目录或单个文件",
    )
    include: Optional[str] = Field(
        None, description='File pattern to include in the search (e.g. "*.js", "*.{ts,tsx}")'
    )


SEARCH_TOOL_DEF = ToolDef(
    name="search",
    description=(
        "快速内容搜索（ripgrep），适配任意规模代码库。按正则匹配文件内容（支持完整正则，"
        "如 log.*Error、function\\s+\\w+）；用 include 按文件名过滤（如 *.js、*.{ts,tsx}）。"
        "返回含匹配的 path:line，按修改时间排序。优先用本工具而不是 run_shell 里的 grep。"
        "需要某符号的真实定义/引用而非文本匹配时，先用本工具定位候选位置。"
    ),
    parameters=SearchParams,
    concurrency_safe=True,
)


def _search_argv(pattern: str, include: Optional[str], targets: list[str]) -> list[str]:
    """Pure: build the rg argv. Mirrors OpenCode searchArgs()."""
    args = ["rg", "--no-config", "--json", "--hidden", "--glob=!.git/*", "--no-messages"]
    if include:
        args.append(f"--glob={include}")
    args += ["--", pattern, *targets]
    return args


def _clean_path(p: str) -> str:
    """Strip a leading ./ or .\\ (mirror OpenCode clean())."""
    if p.startswith("./") or p.startswith(".\\"):
        return p[2:]
    return p


def _parse_rg_json(stdout: str) -> list[dict]:
    """Pure: parse rg --json stream → [{path, line, text}]. Mirrors grep.ts."""
    matches: list[dict] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "match":
            continue
        data = ev.get("data", {})
        path_text = (data.get("path") or {}).get("text")
        line_no = data.get("line_number")
        text = (data.get("lines") or {}).get("text", "")
        if path_text is None or line_no is None:
            continue
        matches.append({
            "path": _clean_path(path_text),
            "line": line_no,
            "text": text.rstrip("\n"),
        })
    return matches


def _format_matches(matches: list[dict], cwd: Path, limit: int = _RESULT_LIMIT) -> str:
    """Pure-ish: sort by mtime desc, group by file, cap + truncate. Mirrors grep.ts."""
    if not matches:
        return "No files found"

    def _mtime(rel: str) -> float:
        try:
            return (cwd / rel).stat().st_mtime
        except OSError:
            return 0.0

    times = {p: _mtime(p) for p in {m["path"] for m in matches}}
    matches.sort(key=lambda m: times.get(m["path"], 0.0), reverse=True)

    total = len(matches)
    truncated = total > limit
    final = matches[:limit] if truncated else matches

    out = [f"Found {total} matches" + (f" (showing first {limit})" if truncated else "")]
    current = None
    for m in final:
        if current != m["path"]:
            if current is not None:
                out.append("")
            current = m["path"]
            out.append(f"{m['path']}:")
        text = m["text"]
        if len(text) > _MAX_LINE_LENGTH:
            text = text[:_MAX_LINE_LENGTH] + "..."
        out.append(f"  Line {m['line']}: {text}")

    if truncated:
        out.append("")
        out.append(
            f"(Results truncated: showing {limit} of {total} matches "
            f"({total - limit} hidden). Consider using a more specific path or pattern.)"
        )
    return "\n".join(out)


async def _run_search(pattern: str, root: Path, include: Optional[str]) -> str:
    """Run rg inside the confined root and format like OpenCode's grep tool."""
    # dir → search '.'; single file → search just that file (mirror grep.ts)
    if root.is_file():
        cwd, targets = root.parent, [root.name]
    else:
        cwd, targets = root, ["."]

    argv = _search_argv(pattern, include, targets)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=_sandbox_env(),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_SEARCH_TIMEOUT_S)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return f"[搜索超时] 超过 {_SEARCH_TIMEOUT_S} 秒，请缩小 path 或收紧正则"
    except FileNotFoundError:
        return "[系统错误] 未找到 ripgrep（rg）"

    # rg exit: 0 = matches, 1 = no matches, >=2 = error (--no-messages mutes stderr noise)
    if proc.returncode not in (0, 1):
        detail = err.decode(errors="replace").strip() or f"exit {proc.returncode}"
        return f"[搜索错误] {detail}"

    matches = _parse_rg_json(out.decode(errors="replace"))
    return _format_matches(matches, cwd)


async def _handle_search(
    pattern: str,
    path: Optional[str] = None,
    include: Optional[str] = None,
    context: dict = None,
) -> str:
    ctx = context or {}
    if not pattern:
        return "[参数错误] pattern 不能为空"
    if shutil.which("rg") is None:
        return "[系统错误] 未找到 ripgrep（rg），请安装 ripgrep（与 OpenCode 同款依赖）"
    # Confine the search root to the group workspace (same boundary as run_shell);
    # an out-of-bounds path is rejected, so search can't read sibling groups.
    root, err = _resolve_shell_cwd(path or "", ctx.get("bot_id"), ctx.get("group_id"))
    if err:
        return f"[安全拒绝] {err}"
    return await _run_search(pattern, root, include)

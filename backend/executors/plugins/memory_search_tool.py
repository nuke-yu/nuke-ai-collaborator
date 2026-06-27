"""L3 — 三层工具记忆检索 builtin（零模型）。

借鉴 claude-mem 的 3-layer workflow：先 search 拿 index+ID（便宜），筛完再 fetch
取全文（贵），中间可用 timeline 看 anchor 周围时序。10x token 节省的关键是
index→fetch 的分离，而非语义 vs 关键词——v1 走 SQLite 关键词足矣。

读取严格按 context 里的 group_id 过滤（群组隔离铁律），数据来自 ai/tool_events 的
tool_events 表。这些工具留在 tool_executor registry（builtin），因此 before-hook
权限检查照常触发。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from executors.base import ToolDef


def _fmt_ts(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
    except Exception:
        return "?"


def _files_or_cmd(row: dict) -> str:
    cmd = row.get("command")
    if cmd:
        return f"$ {cmd[:80]}"
    import json
    try:
        files = json.loads(row.get("files_touched") or "[]")
    except Exception:
        files = []
    return ", ".join(files[:3]) if files else ""


def _render_index(rows: list[dict], header: str) -> str:
    if not rows:
        return "（无匹配事件）"
    lines = [header, "ID    | 时间        | 工具            | 文件/命令"]
    for r in rows:
        err = " ⚠" if r.get("is_error") else ""
        lines.append(
            f"{r['id']:<5} | {_fmt_ts(r['ts'])} | {r['tool'][:15]:<15}{err} | {_files_or_cmd(r)}"
        )
    lines.append("\n下一步：用 memory_fetch(ids=[...]) 仅对相关 ID 取全文；或 memory_timeline(anchor=ID) 看周围时序。")
    return "\n".join(lines)


# ── search ────────────────────────────────────────────────────────────────
class MemorySearchParams(BaseModel):
    query: str = Field("", description="关键词（多词按 AND 匹配工具名/入参/结果/命令/文件）；留空=最近 N 条")
    limit: int = Field(20, description="最多返回条数（默认 20，上限 100）")
    tool: Optional[str] = Field(None, description="只看某个工具名，如 run_shell / edit_file")


SEARCH_MEMORY_TOOL_DEF = ToolDef(
    name="search_memory",
    description=(
        "【工具记忆 · 第1层】检索本群过往工具调用，只返回 index（ID+时间+工具+文件/命令），便宜。"
        "工作流：先用本工具拿到候选 ID，再用 memory_fetch 仅对相关 ID 取全文（省 ~10x token）；"
        "需要看某条周围发生了什么用 memory_timeline。回忆'之前改过哪个文件/跑过什么命令/在哪报过错'时优先用本工具。"
    ),
    parameters=MemorySearchParams,
    concurrency_safe=True,
)


async def _handle_search_memory(query: str = "", limit: int = 20,
                                tool: Optional[str] = None, context: dict = None) -> str:
    ctx = context or {}
    gid = ctx.get("group_id")
    if gid is None:
        return "[错误] 无群组上下文，无法检索记忆"
    from ai.tool_events import search_events
    rows = await search_events(gid, query or "", limit=limit, tool=tool)
    label = f'"{query}"' if query else "最近事件"
    return _render_index(rows, f"工具记忆检索 · {label} · {len(rows)} 条")


# ── timeline ────────────────────────────────────────────────────────────────
class MemoryTimelineParams(BaseModel):
    anchor: int = Field(..., description="锚点事件 ID（来自 search_memory 的结果）")
    before: int = Field(3, description="锚点之前的条数（默认 3，上限 20）")
    after: int = Field(3, description="锚点之后的条数（默认 3，上限 20）")


MEMORY_TIMELINE_TOOL_DEF = ToolDef(
    name="memory_timeline",
    description=(
        "【工具记忆 · 第2层】围绕某个事件 ID 取前后时间线（index 行），看'当时在做什么'。"
        "先用 search_memory 找到 anchor，再用本工具展开上下文。"
    ),
    parameters=MemoryTimelineParams,
    concurrency_safe=True,
)


async def _handle_memory_timeline(anchor: int, before: int = 3, after: int = 3,
                                  context: dict = None) -> str:
    ctx = context or {}
    gid = ctx.get("group_id")
    if gid is None:
        return "[错误] 无群组上下文，无法检索记忆"
    from ai.tool_events import timeline_events
    rows = await timeline_events(gid, anchor, before=before, after=after)
    return _render_index(rows, f"时间线 · anchor={anchor} · {len(rows)} 条")


# ── fetch ────────────────────────────────────────────────────────────────
class MemoryFetchParams(BaseModel):
    ids: list[int] = Field(..., description="要取全文的事件 ID 数组（来自 search_memory，上限 50）")


MEMORY_FETCH_TOOL_DEF = ToolDef(
    name="memory_fetch",
    description=(
        "【工具记忆 · 第3层】对筛过的事件 ID 取完整入参+结果。只在用 search_memory 筛出相关 ID 后调用——"
        "别不过滤直接拉全文。一次可批量传多个 ID。"
    ),
    parameters=MemoryFetchParams,
    concurrency_safe=True,
)


# Surfaced to the model via tool_loop_v1's manifest (recall is a default
# capability, like read_file/run_shell); registered for execution in
# register_workspace_tools.
MEMORY_TOOLS = [SEARCH_MEMORY_TOOL_DEF, MEMORY_TIMELINE_TOOL_DEF, MEMORY_FETCH_TOOL_DEF]


async def _handle_memory_fetch(ids: list[int], context: dict = None) -> str:
    ctx = context or {}
    gid = ctx.get("group_id")
    if gid is None:
        return "[错误] 无群组上下文，无法检索记忆"
    if not ids:
        return "[参数错误] ids 不能为空"
    from ai.tool_events import fetch_events
    rows = await fetch_events(gid, ids)
    if not rows:
        return "（未找到对应 ID 的事件）"
    blocks = []
    for r in rows:
        err = " ⚠出错" if r["is_error"] else ""
        head = f"#{r['id']} · {_fmt_ts(r['ts'])} · {r['tool']}{err}"
        fc = _files_or_cmd(r)
        meta = f"\n  files/cmd: {fc}" if fc else ""
        blocks.append(
            f"{head}{meta}\n  入参: {r['args_summary']}\n  结果: {r['result_summary']}"
        )
    return "\n\n".join(blocks)

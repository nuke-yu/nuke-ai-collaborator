"""L1 — 确定性工具事件日志（零 LLM）。

每次工具调用（builtin / skill / shell / MCP）经 executors/tool_dispatch.dispatch_tool
收口后，把"发生了什么"用纯代码抽成一行结构化事件，落 per-group DB 的 tool_events 表。
**fire-and-forget，失败吞掉**——记忆层永不拖垮主 tool loop（镜像 claude-mem 的 fail-open）。

不调用任何模型：args/result 只做 redact + 截断，files/command 从入参直接抠。这是
"发生了什么"的可召回底座；提炼成持久记忆是上层（bot 自驱 observe / 可选 L4）的事。

DB 路由复用 ai.memory._memory_db：tool_events 是 GROUP 表，分库模式自动落群私有库，
单库 / 测试模式落默认库——与 role_summaries / reflection_state 行为完全一致。
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger(__name__)

# 事件 summary 的字段上限（字符）。事件日志只要"够认出是什么"，不需要全文——全文留在
# 主 loop 的 tool message 里。head/tail 各留一截，中间塞省略标记，避免一个巨型 Read
# 把单行 summary 撑爆（claude-mem 16k 那招的轻量版）。
_SUMMARY_MAX_CHARS = 2_000
_SUMMARY_HEAD = 0.6
_SUMMARY_TAIL = 0.3

# path 类入参的键名（workspace_tools 统一用 path；兼容其它工具的常见叫法）。
_FILE_KEYS = ("path", "file_path", "file", "filepath")
# shell 命令键名（run_shell 用 cmd；兼容 command）。
_CMD_KEYS = ("cmd", "command")
_SHELL_TOOLS = ("run_shell", "bash", "shell")


def _summarize(value, cap: int = _SUMMARY_MAX_CHARS) -> str:
    """JSON 化 → redact 机密 → head/tail 截断。纯函数，零模型。"""
    if isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            raw = str(value)
    try:
        from executors.redaction import redact_secrets
        raw, _ = redact_secrets(raw)
    except Exception:
        pass  # redaction 不可用也不该挡住事件落库
    if len(raw) <= cap:
        return raw
    head = int(cap * _SUMMARY_HEAD)
    tail = int(cap * _SUMMARY_TAIL)
    elided = len(raw) - head - tail
    return f"{raw[:head]}\n…<elided {elided} chars>…\n{raw[-tail:]}"


def _extract_files(arguments: dict) -> list[str]:
    """从入参抠出涉及的文件路径（read/write/edit 都用 path）。纯代码。"""
    if not isinstance(arguments, dict):
        return []
    files: list[str] = []
    for key in _FILE_KEYS:
        v = arguments.get(key)
        if isinstance(v, str) and v:
            files.append(v)
    # 多文件批改：edits/paths 数组里也可能带 path
    edits = arguments.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict):
                p = e.get("path") or e.get("file_path")
                if isinstance(p, str) and p:
                    files.append(p)
    paths = arguments.get("paths")
    if isinstance(paths, list):
        files.extend(p for p in paths if isinstance(p, str) and p)
    # 去重保序
    seen: set[str] = set()
    return [f for f in files if not (f in seen or seen.add(f))]


def _extract_command(tool: str, arguments: dict) -> str | None:
    if tool not in _SHELL_TOOLS or not isinstance(arguments, dict):
        return None
    for key in _CMD_KEYS:
        v = arguments.get(key)
        if isinstance(v, str) and v:
            return v[:_SUMMARY_MAX_CHARS]
    return None


async def record_event(
    *,
    group_id: int | None,
    bot_id: int | None,
    tool: str,
    arguments: dict,
    result,
    is_error: bool,
    thread_id: str | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
    attempt_id: str | None = None,
) -> None:
    """把一次工具调用写成一行 tool_events。group_id 缺失即跳过（测试/最小 loop）。"""
    if group_id is None or not tool:
        return
    try:
        from ai.memory import _memory_db
        row = (
            int(time.time() * 1000),
            group_id,
            bot_id,
            thread_id or "",
            tool,
            _summarize(arguments),
            _summarize(result),
            1 if is_error else 0,
            json.dumps(_extract_files(arguments), ensure_ascii=False),
            _extract_command(tool, arguments),
            run_id or "",
            step_id or "",
            attempt_id or "",
        )
        async with await _memory_db("tool_events", group_id, write=True) as db:
            await db.execute(
                "INSERT INTO tool_events "
                "(ts, group_id, bot_id, thread_id, tool, args_summary, result_summary, "
                " is_error, files_touched, command, run_id, step_id, attempt_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            await db.commit()
    except Exception:
        from db.errors import is_missing_schema_error
        import sys
        e = sys.exc_info()[1]
        if e is not None and is_missing_schema_error(e):
            # 缺表/缺列是迁移缺口，响亮上抛而非当成"没记成"咽下（对齐 ai.memory 约定）。
            raise
        log.debug("record_event swallowed (group_id=%s, tool=%s)", group_id, tool, exc_info=True)


# ───────────────────────── L3 三层检索（读路径，零模型） ─────────────────────────
# search → 只返回 index（id/tool/files/cmd，便宜）；timeline → anchor 周围时间线；
# fetch → 仅对筛过的 id 取全文（贵）。读路径同样复用 _memory_db 解析到同一群库。

# 拼成一段可搜文本，让一次 LIKE 覆盖 tool/入参/结果/命令/文件。
_SEARCHABLE = ("tool || ' ' || args_summary || ' ' || result_summary || ' ' "
               "|| IFNULL(command,'') || ' ' || files_touched")
_INDEX_COLS = "id, ts, tool, is_error, files_touched, command"
_FULL_COLS = ("id, ts, group_id, bot_id, thread_id, tool, args_summary, "
              "result_summary, is_error, files_touched, command, run_id, step_id, attempt_id")


def _index_row(r) -> dict:
    return {"id": r[0], "ts": r[1], "tool": r[2], "is_error": bool(r[3]),
            "files_touched": r[4], "command": r[5]}


def _full_row(r) -> dict:
    return {"id": r[0], "ts": r[1], "group_id": r[2], "bot_id": r[3],
            "thread_id": r[4], "tool": r[5], "args_summary": r[6],
            "result_summary": r[7], "is_error": bool(r[8]),
            "files_touched": r[9], "command": r[10], "run_id": r[11],
            "step_id": r[12], "attempt_id": r[13]}


def _fts_match_query(query: str) -> str:
    """把用户输入转成安全的 FTS5 query：每个词作为字面 phrase（双引号包裹、内部引号转义），
    词间空格 = 隐式 AND。这样用户输入里的 FTS5 特殊字符不会引发语法错误（兜底仍有 LIKE）。"""
    terms = [t.replace('"', '""') for t in query.split() if t]
    return " ".join(f'"{t}"' for t in terms)


async def _search_recency(db, group_id, limit, tool) -> list[dict]:
    where = ["group_id = ?"]
    params: list = [group_id]
    if tool:
        where.append("tool = ?")
        params.append(tool)
    params.append(limit)
    async with db.execute(
        f"SELECT {_INDEX_COLS} FROM tool_events WHERE {' AND '.join(where)} "
        f"ORDER BY id DESC LIMIT ?", params,
    ) as cur:
        return [_index_row(r) for r in await cur.fetchall()]


async def _search_like(db, group_id, query, limit, tool) -> list[dict]:
    """关键词 LIKE 兜底：多词 AND 跨整段可搜文本，按时间倒序。"""
    where = ["group_id = ?"]
    params: list = [group_id]
    for term in query.split():
        where.append(f"({_SEARCHABLE}) LIKE ?")
        params.append(f"%{term}%")
    if tool:
        where.append("tool = ?")
        params.append(tool)
    params.append(limit)
    async with db.execute(
        f"SELECT {_INDEX_COLS} FROM tool_events WHERE {' AND '.join(where)} "
        f"ORDER BY id DESC LIMIT ?", params,
    ) as cur:
        return [_index_row(r) for r in await cur.fetchall()]


async def _search_fts(db, group_id, query, limit, tool) -> list[dict]:
    """FTS5 MATCH + bm25 相关性排序（bm25 越小越相关）。"""
    cols = ", ".join(f"te.{c}" for c in _INDEX_COLS.split(", "))
    where = ["f MATCH ?", "te.group_id = ?"]
    params: list = [_fts_match_query(query), group_id]
    if tool:
        where.append("te.tool = ?")
        params.append(tool)
    params.append(limit)
    async with db.execute(
        f"SELECT {cols} FROM tool_events_fts f "
        f"JOIN tool_events te ON te.id = f.rowid "
        f"WHERE {' AND '.join(where)} ORDER BY bm25(f) LIMIT ?", params,
    ) as cur:
        return [_index_row(r) for r in await cur.fetchall()]


async def search_events(group_id: int, query: str = "", limit: int = 20,
                        tool: str | None = None) -> list[dict]:
    """L3-1：检索，返回 index 行（不含全文）。空 query = 最近 N 条。

    非空 query 走 FTS5（MATCH + bm25 相关性排序）；FTS5 不可用或 query 被拒时
    自动降级到 LIKE 关键词（时间倒序）。group_id 强制过滤，绝不跨群。"""
    if group_id is None:
        return []
    limit = max(1, min(int(limit or 20), 100))
    query = (query or "").strip()
    from ai.memory import _memory_db
    async with await _memory_db("tool_events", group_id, write=False) as db:
        if not query:
            return await _search_recency(db, group_id, limit, tool)
        try:
            return await _search_fts(db, group_id, query, limit, tool)
        except Exception:
            return await _search_like(db, group_id, query, limit, tool)


async def timeline_events(group_id: int, anchor: int, before: int = 3,
                          after: int = 3) -> list[dict]:
    """L3-2：anchor 事件周围的时间线（index 行，按时间升序）。id 单调即时序。"""
    if group_id is None or anchor is None:
        return []
    before = max(0, min(int(before or 0), 20))
    after = max(0, min(int(after or 0), 20))
    from ai.memory import _memory_db
    async with await _memory_db("tool_events", group_id, write=False) as db:
        async with db.execute(
            f"SELECT {_INDEX_COLS} FROM tool_events "
            "WHERE group_id=? AND id < ? ORDER BY id DESC LIMIT ?",
            (group_id, anchor, before),
        ) as cur:
            pre = [_index_row(r) for r in await cur.fetchall()][::-1]
        async with db.execute(
            f"SELECT {_INDEX_COLS} FROM tool_events "
            "WHERE group_id=? AND id >= ? ORDER BY id ASC LIMIT ?",
            (group_id, anchor, after + 1),
        ) as cur:
            rest = [_index_row(r) for r in await cur.fetchall()]
    return pre + rest


async def fetch_events(group_id: int, ids: list[int]) -> list[dict]:
    """L3-3：仅对筛过的 id 取全文。group_id 强制过滤防跨群读取。"""
    if group_id is None or not ids:
        return []
    ids = [int(i) for i in ids][:50]
    ph = ",".join("?" * len(ids))
    from ai.memory import _memory_db
    async with await _memory_db("tool_events", group_id, write=False) as db:
        async with db.execute(
            f"SELECT {_FULL_COLS} FROM tool_events "
            f"WHERE group_id=? AND id IN ({ph}) ORDER BY id ASC",
            [group_id, *ids],
        ) as cur:
            return [_full_row(r) for r in await cur.fetchall()]


# ───────────────────── L4 批量压缩（1 次模型调用/触发，无 observer） ─────────────────────
# turn 后由 ChromaMemoryProvider.observe 触发（与 maybe_summarize/maybe_reflect 同列），
# 纯条数门控：某 bot 在某群累计 compressed=0 的事件达到阈值，就用一次 call_ai 把这批总结成
# 1-3 条持久结论，写进 Chroma（mem_type=tool_episode，供 recall / session-init 语义注入），
# 再把这批标记 compressed=1。不引入常驻 observer，成本上限 = 1 次模型调用/触发。

_COMPRESS_PROMPT = (
    "你是工具活动总结助手。下面是某 AI 同事近期的工具调用记录（✓成功/✗出错 工具 文件或命令 → 结果摘要）。"
    "请提炼成对未来工作有用的【持久结论】：改动过/创建过的关键文件、踩过的坑与报错、有效的命令、"
    "得出的事实。每条一行，简洁具体，可在行尾用 `|重要性` 标注 0–1 的分数。"
    "只输出结论行，不要解释；若整批都无值得长期记住的内容，只输出 NO_INSIGHT。\n\n"
)


def _compress_line(r) -> str:
    # r = (id, ts, tool, args_summary, result_summary, is_error, files_touched, command)
    import json as _json
    tag = "✗" if r[5] else "✓"
    detail = r[7]  # command
    if not detail:
        try:
            files = _json.loads(r[6] or "[]")
        except Exception:
            files = []
        detail = ", ".join(files[:3])
    out = (r[4] or "").strip().replace("\n", " ")[:120]
    return f"- {tag} {r[2]} {detail} → {out}".rstrip()


def _parse_insights(text: str, cap: int) -> list[tuple[str, float]]:
    insights: list[tuple[str, float]] = []
    if not text or "NO_INSIGHT" in text:
        return insights
    for line in text.split("\n"):
        line = line.strip().lstrip("-").strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            txt, _, sc = line.rpartition("|")
            txt = txt.strip()
            try:
                score = max(0.0, min(1.0, float(sc.strip())))
            except ValueError:
                txt, score = line, 0.7
        else:
            txt, score = line, 0.7
        if txt:
            insights.append((txt[:500], score))
    return insights[:cap]


async def maybe_compress_tool_events(group_id: int, bot_id: int, role: str = "",
                                     thread_id: str | None = None,
                                     provider: str = "deepseek",
                                     model: str = "deepseek-chat",
                                     strict: bool = False) -> None:
    """L4：条数门控压缩。未达阈值即早退（不调模型）。fail-soft，schema 缺口上抛。"""
    if group_id is None or bot_id is None:
        return
    import asyncio
    from functools import partial
    from core import config
    threshold = config.TOOL_EVENT_COMPRESS_THRESHOLD
    max_batch = config.TOOL_EVENT_COMPRESS_MAX_BATCH
    try:
        from ai.memory import _memory_db, ChromaStore
        async with await _memory_db("tool_events", group_id, write=False) as db:
            async with db.execute(
                "SELECT id, ts, tool, args_summary, result_summary, is_error, "
                "files_touched, command FROM tool_events "
                "WHERE group_id=? AND bot_id=? AND compressed=0 ORDER BY id LIMIT ?",
                (group_id, bot_id, max_batch),
            ) as cur:
                rows = await cur.fetchall()
        if len(rows) < threshold:
            return  # 门控未到：不烧模型

        from ai.client import call_ai_once
        body = "\n".join(_compress_line(r) for r in rows)
        res = await call_ai_once(
            _COMPRESS_PROMPT + body,
            [{"role": "user", "content": "请提炼持久结论。"}],
            provider, model, temperature=0.3, max_tokens=512,
        )
        text = (res.get("content") if isinstance(res, dict) and res.get("type") == "text" else "") or ""
        insights = _parse_insights(text, config.TOOL_EVENT_COMPRESS_MAX_INSIGHTS)

        if insights:
            loop = asyncio.get_running_loop()
            max_ts = max(r[1] for r in rows) / 1000.0  # ms → s，对齐 time.time() 基准
            for idx, (insight, score) in enumerate(insights):
                ts = max_ts + (idx + 1) * 0.001
                metadata = {
                    "bot_id": bot_id,
                    "role": role or "",
                    "timestamp": ts,
                    "importance": score,
                    "mem_type": "tool_episode",      # 区别于 fact / reflection
                    "thread_id": thread_id or "",
                    "scored_by_model": f"{provider}/{model}",
                }
                if group_id is not None:
                    metadata["group_id"] = group_id
                fid = f"toolsum_{bot_id}_{group_id}_{int(ts * 1000)}_{idx}"
                await loop.run_in_executor(
                    None, partial(ChromaStore.write_fact_sync, fid, insight, metadata)
                )

        # 无论是否有洞察都推进 compressed（NO_INSIGHT 也推进，避免下一轮重复压同一批）。
        ids = [r[0] for r in rows]
        ph = ",".join("?" * len(ids))
        async with await _memory_db("tool_events", group_id, write=True) as db:
            await db.execute(
                f"UPDATE tool_events SET compressed=1 WHERE id IN ({ph})", ids
            )
            await db.commit()
        log.info("compressed %d tool_events → %d insight(s) (group=%s, bot=%s)",
                 len(rows), len(insights), group_id, bot_id)

        # 低概率后台清理：已压缩且超保留期的原始行只是审计冗余，删之防表无限增长。
        import random
        if random.random() < 0.1:
            await _prune_compressed(group_id)
    except Exception:
        from db.errors import is_missing_schema_error
        import sys
        e = sys.exc_info()[1]
        if strict or (e is not None and is_missing_schema_error(e)):
            raise
        log.debug("maybe_compress_tool_events swallowed (group=%s, bot=%s)",
                  group_id, bot_id, exc_info=True)


async def _prune_compressed(group_id: int) -> None:
    """删除 compressed=1 且超过保留天数的原始事件行。"""
    import time as _time
    from core import config
    from ai.memory import _memory_db
    cutoff_ms = int((_time.time() - config.TOOL_EVENT_RETENTION_DAYS * 86400) * 1000)
    async with await _memory_db("tool_events", group_id, write=True) as db:
        await db.execute(
            "DELETE FROM tool_events WHERE group_id=? AND compressed=1 AND ts < ?",
            (group_id, cutoff_ms),
        )
        await db.commit()

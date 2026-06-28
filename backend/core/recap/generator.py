import logging
import db
from db import get_db, write_connect, get_members, get_messages
from ai.client import call_ai_once
from bus import bus

log = logging.getLogger(__name__)

_generating_groups = set()
_last_generated = {}

# debounce 时间戳只用于 5s 去抖；超过这个 TTL 的条目早已失效，定期清掉以免
# _last_generated 随群数无界增长。
_DEBOUNCE_TTL = 60


def _prune_last_generated(now: float) -> None:
    cutoff = now - _DEBOUNCE_TTL
    for gid in [g for g, t in _last_generated.items() if t < cutoff]:
        del _last_generated[gid]


from contextlib import nullcontext
import os

def _maybe_bind_group_db(group_id: int):
    from db.context import current_db_path
    if current_db_path.get():
        return nullcontext()
    from runtime.dbpaths import group_db_path
    import db as _db
    g_path = os.path.abspath(group_db_path(group_id))
    c_path = os.path.abspath(_db.DB_PATH)
    if g_path == c_path or "test" in os.path.basename(c_path):
        return nullcontext()
    return _db.bind_db(group_db_path(group_id))


_GROUP_SYS = (
    "你是一个项目协作助手。请根据提供的最近聊天记录，生成一段 1-3 句的简短“缺席重回”摘要（Recap）。\n"
    "摘要要求：\n"
    "1. 概括当前任务/Ticket 的核心状态。\n"
    "2. 概括各个智能体（Bots，如 BA, Dev, QA）刚刚完成了什么工作。\n"
    "3. 说明下一步计划或当前需要人类用户进行什么操作/确认。\n"
    "4. 语言简洁生动，具有亲和力与科技感，使用中文，总字数控制在 120 字以内。\n"
    "5. 必须直接输出摘要文本，不要包含任何前导词（如“这里是摘要：”）、Markdown 格式标记或解释。"
)

_GROUP_SYS_EN = (
    "You are a project collaboration assistant. Based on the provided recent chat logs, generate a short 1-3 sentence recap for a returning user.\n"
    "Requirements:\n"
    "1. Summarize the core status of the current task/ticket.\n"
    "2. Summarize what each bot agent (e.g., BA, Dev, QA) has just completed.\n"
    "3. Explain the next steps or what action/confirmation is currently required from the human user.\n"
    "4. The language should be concise, lively, friendly, and tech-savvy. Use English, and keep it under 80 words.\n"
    "5. Output the recap text directly. Do not include any prefix (like 'Here is the recap:'), Markdown formatting, or explanations."
)

_PERSONAL_SYS = (
    "你是一个项目协作助手。下面是某位用户离开期间他「错过」的群聊消息。"
    "请用第二人称「你」生成一段 1-3 句的简短摘要，概括：你离开期间发生的关键进展、"
    "各 Bot（BA/Dev/QA）做了什么、以及现在需要你做什么/确认什么。\n"
    "语言简洁生动、使用中文、120 字以内。必须直接输出摘要文本，不要前导词、不要 Markdown 标记。"
)

_PERSONAL_SYS_EN = (
    "You are a project collaboration assistant. Below are the group messages the user missed while away.\n"
    "Please generate a short 1-3 sentence summary using the second person 'you', summarizing: key progress made during your absence, what each bot (BA/Dev/QA) did, and what you need to do/confirm now.\n"
    "The language should be concise and lively. Use English, and keep it under 80 words. Output the recap text directly. No prefixes, no Markdown formatting."
)


def _pick_provider_model(members: list) -> tuple[str, str]:
    """优先 deepseek，否则用第一个 bot 的配置；无 bot 时回退默认。"""
    provider, model = "deepseek", "deepseek-chat"
    bots = [m for m in members if m.get("type") == "bot"]
    if bots:
        preferred = next((b for b in bots if b.get("model_provider") == "deepseek"), bots[0])
        provider = preferred.get("model_provider") or "deepseek"
        model = preferred.get("model_name") or "deepseek-chat"
    return provider, model


async def _summarize(messages: list, members: list, group_id: int, *, personal: bool = False) -> str | None:
    """把一段消息历史摘成一句话 recap（group/personal 共用）。空历史/空结果返回 None。"""
    formatted = []
    for msg in messages:
        if msg.get("is_deleted"):
            continue
        sender = msg.get("sender_name") or "用户"
        formatted.append(f"{sender}: {msg.get('content') or ''}")
    if not formatted:
        return None
    log_text = "\n".join(formatted)
    provider, model = _pick_provider_model(members)
    
    import asyncio
    # Fetch JIRA tickets from Group DB
    tickets_info = []
    try:
        from db import get_db
        async with get_db() as gdb:
            async with gdb.execute(
                "SELECT ticket_id, title, status, project FROM tickets WHERE group_id = ?",
                (group_id,)
            ) as cur:
                rows = await cur.fetchall()
                for r in rows:
                    tickets_info.append(f"- {r[0]}: {r[1]} (状态: {r[2]}, 项目: {r[3] or '未分配'})")
    except Exception as e:
        log.warning(f"Failed to fetch tickets for recap: {e}")

    # Fetch Git Status & Recent Commits
    git_info = []
    try:
        from workspace import layout
        shared_workspace = layout.group_shared_dir(group_id) / "workspace"
        if shared_workspace.exists() and (shared_workspace / ".git").exists():
            # Get recent 5 commits
            proc = await asyncio.create_subprocess_exec(
                "git", "log", "-n", "5", "--oneline",
                cwd=str(shared_workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            commits = stdout.decode("utf-8", errors="ignore").strip()
            if commits:
                git_info.append("最近 Git 提交记录：\n" + commits)
            
            # Get uncommitted status
            proc_status = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                cwd=str(shared_workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_status, _ = await proc_status.communicate()
            status_out = stdout_status.decode("utf-8", errors="ignore").strip()
            if status_out:
                git_info.append("当前工作区未提交的文件变更：\n" + status_out)
    except Exception as e:
        log.warning(f"Failed to fetch git info for recap: {e}")

    extra_parts = []
    if tickets_info:
        extra_parts.append("【Jira 看板任务状态】\n" + "\n".join(tickets_info))
    if git_info:
        extra_parts.append("【Git 代码库最新变更】\n" + "\n".join(git_info))
    extra_context = "\n\n".join(extra_parts)

    from workspace.layout import get_group_language
    lang = get_group_language(group_id)

    if lang == "en":
        system_prompt = _PERSONAL_SYS_EN if personal else _GROUP_SYS_EN
        user_message = (
            f"Here are the group messages you missed while away:\n\n{log_text}"
            if personal else
            f"Here are the recent chat logs from the project collaboration:\n\n{log_text}"
        )
        if extra_context:
            user_message += f"\n\nHere is the current system state (Git changes & Jira tickets status):\n{extra_context}"
        if personal:
            user_message += "\n\nPlease generate your recap."
        else:
            user_message += "\n\nPlease generate a short 1-3 sentence recap for a returning user."
    else:
        system_prompt = _PERSONAL_SYS if personal else _GROUP_SYS
        user_message = (
            f"以下是你离开期间错过的群聊消息：\n\n{log_text}"
            if personal else
            f"以下是项目协作中最近的聊天记录：\n\n{log_text}"
        )
        if extra_context:
            user_message += f"\n\n以下是当前系统实际发生的数据变更（Git 代码库与 Jira 任务看板最新状态）：\n{extra_context}"
        if personal:
            user_message += "\n\n请生成你的「缺席重回」摘要。"
        else:
            user_message += "\n\n请为重回项目的用户生成一段 1-3 句的简短“缺席重回”摘要（Recap）。"

    res = await call_ai_once(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        provider=provider, model=model, temperature=0.5, max_tokens=256,
    )
    summary = (res.get("content") if isinstance(res, dict) else str(res)) or ""
    return summary.strip() or None

async def generate_and_cache_recap(group_id: int, force: bool = False) -> str | None:
    """
    Pre-generates a 1-3 sentence recap for the group and caches it in the groups table of the central DB.
    Does not block workflow execution; failures are caught and logged.

    force=True (用户手动触发) skips the 5s recency debounce so an explicit
    "regenerate" always recomputes. The in-flight guard still applies — the API
    layer falls back to the cached summary when a concurrent run returns None,
    so the banner is never blanked.
    """
    import time
    _prune_last_generated(time.time())

    if group_id in _generating_groups:
        log.info("Recap generation already in progress for group %s, skipping", group_id)
        return None

    if not force:
        now = time.time()
        if now - _last_generated.get(group_id, 0) < 5:
            log.info("Recap generated too recently for group %s, skipping eager run", group_id)
            return None

    _generating_groups.add(group_id)
    try:
        with _maybe_bind_group_db(group_id):
            log.info("Starting recap generation for group %s", group_id)
            # 1. Fetch members from Central DB and messages from Group Private DB
            from db import global_db
            async with global_db() as cdb:
                members = await get_members(cdb, group_id)
            async with get_db() as gdb:
                messages = await get_messages(gdb, group_id, limit=30)
            
            if not messages:
                log.info("No messages found in group %s to summarize", group_id)
                return None

            # 2-5. Summarize the recent group activity (shared helper)
            summary = await _summarize(messages, members, group_id)
            if not summary:
                log.warning("Generated empty recap for group %s", group_id)
                return None

            # 6. Save the summary in the database (groups table in the central DB, hence passing db.DB_PATH explicitly)
            async with write_connect(db.DB_PATH) as db_conn:
                await db_conn.execute(
                    "UPDATE groups SET away_summary = ? WHERE id = ?",
                    (summary, group_id)
                )
                await db_conn.commit()
                
            log.info("Recap generated and cached successfully for group %s: %s", group_id, summary)
            _last_generated[group_id] = time.time()
            
            # 7. Broadcast the updated recap to websocket clients
            await bus.broadcast(group_id, {
                "type": "recap_updated",
                "group_id": group_id,
                "away_summary": summary
            })
            
            return summary
    except Exception as e:
        log.error("Failed to generate and cache recap for group %s: %r", group_id, e, exc_info=True)
        return None
    finally:
        _generating_groups.discard(group_id)

async def clear_recap(group_id: int) -> None:
    """
    Clears the cached away summary from the groups table in the central DB.
    """
    try:
        # Pass db.DB_PATH explicitly to write to the central DB
        async with write_connect(db.DB_PATH) as db_conn:
            await db_conn.execute(
                "UPDATE groups SET away_summary = NULL WHERE id = ?",
                (group_id,)
            )
            await db_conn.commit()
        log.info("Away summary cleared for group %s", group_id)
        
        # Broadcast the clear event to websocket clients
        await bus.broadcast(group_id, {
            "type": "recap_updated",
            "group_id": group_id,
            "away_summary": None
        })
    except Exception as e:
        log.error("Failed to clear away summary for group %s: %r", group_id, e, exc_info=True)


async def generate_personal_recap(group_id: int, member_id: int) -> dict:
    """方案 1：按需、不缓存的 per-user recap。概括该成员 last_read_id 之后「错过」的消息。

    返回 {"unread_count": int, "summary": str | None, "covered_through_id": int}。无未读 → summary=None、不调用 LLM。
    成员/消息分属 central/group 库，分别用 global_db()/get_db() 读。
    """
    try:
        with _maybe_bind_group_db(group_id):
            from db import global_db
            async with get_db() as gdb:
                cur = await gdb.execute(
                    "SELECT last_read_id, last_recap_ack_id FROM member_read WHERE member_id = ? AND group_id = ?",
                    (member_id, group_id),
                )
                row = await cur.fetchone()
                last_read = (row[0] if row else 0) or 0
                last_ack = (row[1] if row else 0) or 0
                # 门槛 = max(已读, 已确认)：未读才弹，但点 ✕ (ack) 能压制这批；
                # 对从未离开、已读到最新的用户不会误弹。after_id 升序取门槛之后的消息（cap 30）。
                anchor = max(last_read, last_ack)
                messages = await get_messages(gdb, group_id, limit=30, after_id=anchor)

            if not messages:
                return {"unread_count": 0, "summary": None, "covered_through_id": 0}

            async with global_db() as cdb:
                members = await get_members(cdb, group_id)

            summary = await _summarize(messages, members, group_id, personal=True)
            covered_through_id = messages[-1]["id"]
            return {"unread_count": len(messages), "summary": summary, "covered_through_id": covered_through_id}
    except Exception as e:
        from db.errors import is_missing_schema_error
        if is_missing_schema_error(e):
            raise  # 缺列/缺表是迁移缺口，响亮上抛而非当成"没数据"
        log.error("Failed to generate personal recap for group %s member %s: %r",
                  group_id, member_id, e, exc_info=True)
        return {"unread_count": 0, "summary": None, "covered_through_id": 0}


async def ack_personal_recap(group_id: int, member_id: int, covered_through_id: int | None = None) -> int:
    """记录某成员「已看过」当前 away recap（点 ✕ 触发）。把该用户的 recap 水位线
    (member_read.last_recap_ack_id) 推进到该 recap 实际覆盖的最新消息 id —— 这批活动便不再对他
    显示（重连/切群也不再弹）；之后若有更新的活动，仍会再生成一条只覆盖新活动的摘要。
    每用户独立：一个人点 ✕ 不影响其他成员。水位线单调不回退。返回推进后的水位线。

    群库由调用方（API 端点）通过 db.bind_db 绑定；读写都走当前绑定的群 DB，与
    generate_personal_recap 一致。失败不抛，返回 0。"""
    try:
        if covered_through_id is not None:
            up_to_id = covered_through_id
        else:
            async with get_db() as gdb:
                cur = await gdb.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM messages WHERE group_id = ?",
                    (group_id,),
                )
                row = await cur.fetchone()
            up_to_id = (row[0] if row else 0) or 0
        # 写路径与读路径同源：绑定时 = 当前群库，未绑定（如单库测试）= db.DB_PATH。
        write_path = db.current_db_path.get() or db.DB_PATH
        async with write_connect(write_path) as w:
            await w.execute(
                "INSERT INTO member_read (member_id, group_id, last_recap_ack_id) VALUES (?,?,?) "
                "ON CONFLICT(member_id, group_id) DO UPDATE SET "
                "last_recap_ack_id = MAX(member_read.last_recap_ack_id, excluded.last_recap_ack_id)",
                (member_id, group_id, up_to_id),
            )
            await w.commit()
        return up_to_id
    except Exception as e:
        from db.errors import is_missing_schema_error
        if is_missing_schema_error(e):
            raise  # 缺列/缺表是迁移缺口，响亮上抛而非当成"没数据"
        log.error("Failed to ack personal recap for group %s member %s: %r",
                  group_id, member_id, e, exc_info=True)
        return 0

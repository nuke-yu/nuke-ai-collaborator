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
        
        # 2. Determine LLM provider and model based on group's bot configurations
        provider = "deepseek"
        model = "deepseek-chat"
        
        bots = [m for m in members if m.get("type") == "bot"]
        if bots:
            # Prefer deepseek if any bot uses it, otherwise use the first bot's configuration
            preferred_bot = next((b for b in bots if b.get("model_provider") == "deepseek"), bots[0])
            provider = preferred_bot.get("model_provider") or "deepseek"
            model = preferred_bot.get("model_name") or "deepseek-chat"
            
        # 3. Format message history for LLM context
        formatted_history = []
        for msg in messages:
            if msg.get("is_deleted"):
                continue
            sender = msg.get("sender_name") or "用户"
            content = msg.get("content") or ""
            formatted_history.append(f"{sender}: {content}")
            
        log_text = "\n".join(formatted_history)
        
        # 4. Set up system prompt and user message
        system_prompt = (
            "你是一个项目协作助手。请根据提供的最近聊天记录，生成一段 1-3 句的简短“缺席重回”摘要（Recap）。\n"
            "摘要要求：\n"
            "1. 概括当前任务/Ticket 的核心状态。\n"
            "2. 概括各个智能体（Bots，如 BA, Dev, QA）刚刚完成了什么工作。\n"
            "3. 说明下一步计划或当前需要人类用户进行什么操作/确认。\n"
            "4. 语言简洁生动，具有亲和力与科技感，使用中文，总字数控制在 120 字以内。\n"
            "5. 必须直接输出摘要文本，不要包含任何前导词（如“这里是摘要：”）、Markdown 格式标记或解释。"
        )
        
        user_message = f"以下是项目协作中最近的聊天记录：\n\n{log_text}\n\n请为重回项目的用户生成一段 1-3 句的简短“缺席重回”摘要（Recap）。"
        
        # 5. Call LLM to generate summary
        res = await call_ai_once(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            provider=provider,
            model=model,
            temperature=0.5,
            max_tokens=256
        )
        
        summary = ""
        if isinstance(res, dict):
            summary = res.get("content") or ""
        else:
            summary = str(res)
            
        summary = summary.strip()
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

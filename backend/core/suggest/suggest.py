import logging
import json
import re
import time
from pathlib import Path
import db
from db import get_db, write_connect, get_members, get_messages
from workspace import group_workspace
from ai.client import call_ai_once

log = logging.getLogger(__name__)

# Concurrency lock & Cache dictionary
_suggesting_groups = set()
_suggest_cache = {}

_SUGGEST_SYSTEM_PROMPT = """\
你是一个项目协作助手。请根据提供的最近开发对话与执行历史，为人类用户生成 2-3 条下一步可能想发送给机器人的自然语言消息草稿（Smart-Compose）。

当前群组中可供 @ 提及的智能体（Bots）名单如下：
{bot_list}

规则（安全与质量护栏）：
1. 每条建议必须以 `@真实名称` 开头，且只使用名单中给出的真实名称进行 @ 提及。
2. 每条建议应该是具体的、具有行动力的自然语言工作指示（例如：指令机器人去读取某个文件、运行某项测试、编写某段代码、排查某个报错）。
3. 绝对不要生成任何系统控制命令或斜杠命令（如 /confirm、/abort 等），你只需提供供用户与智能体沟通的自然语言草稿。
4. 每条建议长度必须在 60 字以内，使用中文。
5. 必须以 Markdown 列表格式输出（每行以 - 开头），不要包含任何前导词或额外解释。
"""


async def get_ai_suggestions(group_id: int, awaiting_confirm: str | None) -> list[str]:
    """
    Generate natural language smart-compose recommendations for users.
    Caches results by (group_id, last_msg_id, awaiting_confirm, bot_names_key).
    Returns cached values instantly when locked, avoiding REST execution stalls.
    """
    # 1. Fetch group members and filter bots to ensure correct cache routing
    members = []
    try:
        from db import global_db
        async with global_db() as cdb:
            members = await get_members(cdb, group_id)
    except Exception as e:
        log.warning("suggest: failed to fetch members: %r", e)

    bots = [m for m in members if m.get("type") == "bot"]
    if not bots:
        return []

    bot_names_key = tuple(sorted(b["name"] for b in bots))

    # 2. Derive last_msg_id from DB
    last_msg_id = 0
    try:
        async with get_db() as gdb:
            latest = await get_messages(gdb, group_id, limit=1)
            if latest:
                last_msg_id = latest[0]["id"]
    except Exception as e:
        log.warning("suggest: failed to resolve last_msg_id: %r", e)

    cache_key = (group_id, last_msg_id, awaiting_confirm, bot_names_key)

    # 3. Check Cache
    if cache_key in _suggest_cache:
        val, ts = _suggest_cache[cache_key]
        if val or (time.time() - ts < 30):
            return val

    # 4. Check Concurrency Lock (Non-blocking fallback)
    if group_id in _suggesting_groups:
        log.info("suggest: concurrent request locked for group %s, returning old cache fallback", group_id)
        # Find any old cached result for this group
        old_val = next((v[0] for k, v in _suggest_cache.items() if k[0] == group_id), [])
        return old_val

    _suggesting_groups.add(group_id)
    try:
        bot_list_str = "\n".join([f"- {b['name']} (角色: {b.get('role') or '机器人'})" for b in bots])

        # 5. Fetch recent 20 messages for conversational context
        async with get_db() as gdb:
            recent_msgs = await get_messages(gdb, group_id, limit=20)
        
        # Filter out deleted messages
        active_msgs = [m for m in recent_msgs if not m.get("is_deleted")]
        history_text = "\n".join([f"{m.get('sender_name', 'User')}: {(m.get('content') or '')[:1000]}" for m in active_msgs])

        # 6. Read RETRO_LATEST.md with size-cap and fallback
        retro_chunk = ""
        try:
            ws_path = group_workspace(group_id)
            retro_file = ws_path / "RETRO_LATEST.md"
            if retro_file.exists():
                retro_chunk = retro_file.read_text(encoding="utf-8")[:1000]
        except Exception as e:
            log.warning("suggest: failed to read RETRO_LATEST.md: %r", e)

        # 7. Model selection and LLM invocation
        from core.recap.generator import _pick_provider_model
        provider, model = _pick_provider_model(members)

        status_context = f"等待用户确认 (卡片ID: {awaiting_confirm})" if awaiting_confirm else "处于自由交流状态"

        user_message = (
            f"当前工作流状态：{status_context}\n\n"
            f"最近的复盘摘要片段（可能为空）：\n{retro_chunk}\n\n"
            f"最近 20 条消息上下文：\n{history_text}\n\n"
            "请为用户生成 2-3 条下一步的沟通消息草稿。"
        )

        log.info("suggest: calling LLM (%s/%s) for group %s", provider, model, group_id)
        res = await call_ai_once(
            system_prompt=_SUGGEST_SYSTEM_PROMPT.format(bot_list=bot_list_str),
            messages=[{"role": "user", "content": user_message}],
            provider=provider, model=model, temperature=0.5, max_tokens=256
        )
        content = (res.get("content") if isinstance(res, dict) else str(res)) or ""
        content = content.strip()

        # 8. Clean and parse LLM outputs (supporting bullet & numeric formats)
        suggestions = []
        if content:
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("- ") or line.startswith("* ") or re.match(r"^\d+[\.\)]\s+", line):
                    cleaned = re.sub(r"^[-*\s\d\.\)]+", "", line).strip()
                    if cleaned:
                        suggestions.append(cleaned)

        suggestions = suggestions[:3]  # Limit to max 3 items

        # 9. Cache update with memory pool guard (always cache with timestamp)
        if len(_suggest_cache) > 200:
            log.info("suggest: memory cache cleared (limit reached)")
            _suggest_cache.clear()

        _suggest_cache[cache_key] = (suggestions, time.time())
        return suggestions

    except Exception as e:
        log.error("Failed to generate AI suggestions for group %s: %r", group_id, e, exc_info=True)
        return []
    finally:
        _suggesting_groups.discard(group_id)

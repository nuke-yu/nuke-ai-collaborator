import asyncio
import uuid
from db import get_db, get_messages, save_message, get_members, get_group
from bus import bus
from bus.events import (
    StreamStart, StreamChunk, StreamError, StreamEnd,
    Message, Read, Typing, Error,
    SteerQueued, FollowupStart,
    TicketCreated,  # TODO 1.3
)

...

async def init_event_handlers():
    """Initialize background event listeners for R&D automation."""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("Orchestrator: registering R&D event handlers...")
    
    async def _on_ticket_created(ev: dict):
        group_id = ev["group_id"]
        ticket_id = ev["ticket_id"]
        title = ev["title"]
        
        async with get_db() as db:
            all_members = await get_members(db, group_id)
            group_info = await get_group(db, group_id) or {}
            
        all_bots = [m for m in all_members if m["type"] == "bot"]
        # Find Dev bots
        dev_bots = [b for b in all_bots if (b.get("role") or "").lower() == "dev"]
        
        if not dev_bots:
            logger.warning("TicketCreated: no Dev bots found in group %d", group_id)
            return

        # Simple strategy: select the first one for now
        target_bot = dev_bots[0]
        
        content = f"[系统通知] 发现新任务：{title} ({ticket_id})。请认领并开始执行。"
        sender = {"id": 0, "name": "系统调度", "type": "system"}
        
        async with get_db() as db_m:
            recent = await get_messages(db_m, group_id)
            
        logger.info("TicketCreated: auto-dispatching bot %s for ticket %s", target_bot["name"], ticket_id)
        
        # Trigger the bot
        await dispatch_bots(
            group_id, [target_bot], content, sender, recent,
            all_bots, all_members,
            group_name=group_info.get("name", ""),
            group_announcement=group_info.get("announcement", ""),
        )

    async def _listener_loop():
        sub = bus.subscribe(TicketCreated)
        async with sub:
            async for ev in sub:
                try:
                    await _on_ticket_created(ev)
                except Exception:
                    logger.exception("Error in TicketCreated handler")

    asyncio.create_task(_listener_loop())
from ai.client import call_ai, call_ai_stream, call_ai_once, AIError
from core.role_router import should_bot_respond, build_context_message
from ai.memory import maybe_summarize, get_memory_context, add_to_chroma
from executors.base import ExecutionContext
from executors import registry
from core import bg
import core.workflow as wf

from core.orchestration import locks

# Steer registry: (group_id, bot_id) -> Queue of injected messages for running bots
_steer_queues: dict[tuple[int, int], asyncio.Queue] = {}

_MAX_FOLLOWUP_DEPTH = 5  # Max chained follow-up runs per bot session


def _start_steer(group_id: int, bot_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _steer_queues[(group_id, bot_id)] = q
    return q


def _stop_steer(group_id: int, bot_id: int) -> None:
    _steer_queues.pop((group_id, bot_id), None)


def get_steer_queue(group_id: int, bot_id: int) -> asyncio.Queue | None:
    return _steer_queues.get((group_id, bot_id))


def _with_personality(base_prompt: str, bot: dict) -> str:
    p = (bot.get("personality_prompt") or "").strip()
    return base_prompt + f"\n\n【性格指令】\n{p}" if p else base_prompt


async def select_triggered_bots(content: str, all_bots: list, group_id: int) -> list[dict]:
    """决定本条消息应该由哪些 bot 响应，同时维护 SQLite 持久化锁。"""
    wf_bot = wf.current_bot(group_id)
    wf_pool = wf.current_pool_bots(group_id)
    if wf_bot:
        return [wf_bot]
    if wf_pool is not None:
        return [b for b in all_bots if b["id"] in wf_pool]

    explicit = [b for b in all_bots if f"@{b['name']}" in content]
    if "@all" in content.lower():
        explicit = all_bots
    if explicit:
        if len(explicit) == 1:
            await locks.set_active_bot(group_id, explicit[0]["id"])
        else:
            await locks.release_lock(group_id)
        return explicit

    locked_bot_id = await locks.get_active_bot(group_id)
    if locked_bot_id:
        locked = next((b for b in all_bots if b["id"] == locked_bot_id), None)
        return [locked] if locked else []

    return [b for b in all_bots if should_bot_respond(content, b["name"], b["role"] or "")]


async def stream_bot_response(group_id: int, bot: dict, system_prompt: str,
                               history: list, user_msg: str):
    """流式广播 bot 回复，保存到 DB，返回 (full_text, msg_id) 或 (None, None)"""
    temp_id = str(uuid.uuid4())
    await bus.publish(StreamStart(
        group_id=group_id, temp_id=temp_id,
        member_id=bot["id"], sender_name=bot["name"],
        sender_type="bot", avatar_color=bot["avatar_color"],
    ))
    provider = bot.get("model_provider", "deepseek")
    model = bot.get("model_name", "deepseek-chat")
    temperature = bot.get("temperature", 0.7)
    max_tokens = bot.get("max_tokens", 4096)
    full_text = ""
    _usage_out: list = []
    try:
        async for chunk in call_ai_stream(system_prompt, history, user_msg, provider, model, temperature, max_tokens, usage_out=_usage_out):
            full_text += chunk
            await bus.publish(StreamChunk(group_id=group_id, temp_id=temp_id, delta=chunk))
    except AIError as e:
        await bus.publish(StreamError(group_id=group_id, temp_id=temp_id, message=str(e)))
        return None, None

    _u = _usage_out[0] if _usage_out else {}
    async with get_db() as db:
        msg_id = await save_message(db, group_id, bot["id"], full_text,
                                    input_tokens=_u.get("input_tokens") or None,
                                    output_tokens=_u.get("output_tokens") or None,
                                    cache_read_tokens=_u.get("cache_read_tokens") or None,
                                    cache_creation_tokens=_u.get("cache_creation_tokens") or None)
        recent = await get_messages(db, group_id)
    await bus.publish(StreamEnd(
        group_id=group_id, temp_id=temp_id, id=msg_id,
        member_id=bot["id"], sender_name=bot["name"],
        preview=full_text[:100],
        created_at=recent[-1]["created_at"] if recent else "",
    ))
    return full_text, msg_id


async def mark_read(group_id: int, member_id: int, msg_id: int):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO member_read (member_id, group_id, last_read_id) VALUES (?,?,?) "
            "ON CONFLICT(member_id, group_id) DO UPDATE SET last_read_id=excluded.last_read_id",
            (member_id, group_id, msg_id)
        )
        await db.commit()
    await bus.publish(Read(group_id=group_id, member_id=member_id, last_read_id=msg_id))


async def send_auto_reply(group_id: int, member: dict, reply_to_id: int):
    await asyncio.sleep(1.5)
    async with get_db() as db:
        msg_id = await save_message(db, group_id, member["id"], member["auto_reply"],
                                    reply_to_id=reply_to_id, is_auto_reply=True)
        recent = await get_messages(db, group_id)
        saved = next((m for m in recent if m["id"] == msg_id), {})
    await bus.publish(Message(group_id=group_id, **{k: v for k, v in saved.items() if k != "group_id"}))


async def auto_continue_if_needed(group_id: int, bot: dict, all_members: list, max_iter: int = 5):
    """开发类角色没写完（没有 @下一个人）时，自动续写直到完成"""
    if "开发" not in (bot["role"] or ""):
        return
    for _ in range(max_iter):
        async with get_db() as db:
            recent = await get_messages(db, group_id, limit=10)
        latest = next((m for m in reversed(recent) if m["member_id"] == bot["id"]), None)
        if not latest:
            break
        if any(f"@{m['name']}" in latest["content"] for m in all_members):
            break
        history, _ = build_context_message("", "系统", recent)
        system_prompt = _with_personality(bot["system_prompt"] or f"你是{bot['name']}，{bot['role']}。", bot)
        last_user_msg = next((m["content"] for m in reversed(recent) if m["sender_type"] != "bot"), "继续代码")
        memory = await get_memory_context(bot["id"], bot["role"] or "", last_user_msg)
        if memory:
            system_prompt += f"\n\n{memory}"
        ai_reply, bot_msg_id = await stream_bot_response(
            group_id, bot, system_prompt, history,
            "请继续上面未完成的代码，直到所有功能全部实现完毕。完成后在最后一行写上交接信息。"
        )
        if not ai_reply:
            break
        bg.spawn(add_to_chroma(bot_msg_id, ai_reply, bot["role"] or "", bot["id"]))
        bg.spawn(maybe_summarize(group_id, bot["id"], bot["role"] or bot["name"], [bot["id"]]))
        if any(f"@{m['name']}" in ai_reply for m in all_members):
            break


async def check_handoff(group_id: int, all_bots: list, all_members: list,
                         context_messages: list, _depth: int = 0):
    """检测最新 bot 消息中的 @mention，自动通知对应角色开始工作"""
    if _depth > 5:
        return
    async with get_db() as db:
        recent = await get_messages(db, group_id, limit=5)
    latest_bot_msg = next((m for m in reversed(recent) if m["sender_type"] == "bot"), None)
    if not latest_bot_msg:
        return
    content = latest_bot_msg["content"]
    sender_id = latest_bot_msg["member_id"]
    mentioned_bots = [b for b in all_bots if f"@{b['name']}" in content and b["id"] != sender_id]
    if not mentioned_bots:
        if any(f"@{m['name']}" in content for m in all_members if m["type"] == "human"):
            await locks.release_lock(group_id)
        return

    human_msgs = [m for m in context_messages if m["sender_type"] != "bot"]
    initiator_name = human_msgs[0]["sender_name"] if human_msgs else "用户"
    target_bot = mentioned_bots[0]

    if "测试" in (target_bot["role"] or ""):
        handoff_prompt = (f"上一阶段完成，以下是交接内容：\n\n{content}\n\n"
                          f"请开始测试。完成所有测试后，在最后一行写：「@{initiator_name} 测试完成，任务全部完成！」")
    else:
        handoff_prompt = f"上一阶段完成，以下是交接内容：\n\n{content}\n\n请开始你的工作。"

    history, _ = build_context_message("", latest_bot_msg["sender_name"], recent)
    system_prompt = _with_personality(target_bot["system_prompt"] or f"你是{target_bot['name']}，{target_bot['role']}。", target_bot)
    ai_reply, bot_msg_id = await stream_bot_response(group_id, target_bot, system_prompt, history, handoff_prompt)
    if not ai_reply:
        return
    bg.spawn(add_to_chroma(bot_msg_id, ai_reply, target_bot["role"] or "", target_bot["id"]))
    await locks.set_active_bot(group_id, target_bot["id"])
    await auto_continue_if_needed(group_id, target_bot, all_members)
    async with get_db() as db_r:
        bot_recent = await get_messages(db_r, group_id)
    await check_handoff(group_id, all_bots, all_members, bot_recent, _depth + 1)


async def dispatch_bots(group_id: int, triggered: list, content: str, sender: dict,
                         recent: list, all_bots: list, all_members: list,
                         group_name: str = "", group_announcement: str = "",
                         file_url: str | None = None, file_type: str | None = None):
    """执行触发的 bot 列表：竞速、顺序、续写、交接。"""
    # Steer running bots instead of launching a new execution for them
    still_triggered = []
    for bot in triggered:
        steer_q = get_steer_queue(group_id, bot["id"])
        if steer_q is not None:
            await steer_q.put(content)
            await bus.publish(SteerQueued(group_id=group_id, member_id=bot["id"], message=content[:300]))
        else:
            still_triggered.append(bot)
    triggered = still_triggered
    if not triggered:
        return

    ctx = {'recent': recent}

    def _bot_recent(bot):
        active = [m for m in ctx['recent'] if not m.get('is_deleted')]
        cleared = bot.get("context_cleared_at")
        if cleared:
            return [m for m in active if m.get("created_at", "") > cleared]
        return active

    human_senders = [m for m in recent if m["sender_type"] != "bot"]
    initiator_name = human_senders[0]["sender_name"] if human_senders else sender["name"]

    async def call_bot(bot):
        history, user_msg = build_context_message(content, sender["name"], _bot_recent(bot))
        base_prompt = _with_personality(bot["system_prompt"] or f"你是{bot['name']}，{bot['role']}。", bot)
        memory = await get_memory_context(bot["id"], bot["role"] or "", content)
        system_prompt = base_prompt + (f"\n\n{memory}" if memory else "")
        if "测试" in (bot["role"] or ""):
            system_prompt += f"\n\n完成测试报告后，在最后一行写：「@{initiator_name} 测试完成，任务全部完成！」"
        result = await call_ai_once(
            system_prompt, history + [{"role": "user", "content": user_msg}],
            provider=bot.get("model_provider", "deepseek"),
            model=bot.get("model_name", "deepseek-chat"),
            temperature=bot.get("temperature", 0.7),
            max_tokens=bot.get("max_tokens", 4096),
        )
        ai_reply = result.get("content", "") if result["type"] == "text" else ""
        return bot, ai_reply, result.get("usage") or {}

    async def race_role_group(bots):
        if len(bots) == 1:
            bot = bots[0]
            steer_q = _start_steer(group_id, bot["id"])
            ctx = ExecutionContext(
                bot=bot, group_id=group_id, user_message=content,
                sender=sender, history=_bot_recent(bot),
                all_bots=all_bots, all_members=all_members,
                broadcaster=bus,
                interaction=interaction.StandardInteraction(),
                workflow_suffix=wf.system_suffix(group_id),
                group_name=group_name,
                group_announcement=group_announcement,
                steer_channel=steer_q,
                file_url=file_url,
                file_type=file_type,
            )
            try:
                result = await registry.get(bot.get("executor_id", "simple_v1")).run(ctx)
            finally:
                _stop_steer(group_id, bot["id"])
            if not result.full_text:
                return
            await wf.check_and_advance(group_id, result.full_text, bot["id"])

            # Process messages that arrived too late to be steered mid-run (followUp chain)
            for _ in range(_MAX_FOLLOWUP_DEPTH):
                if steer_q.empty():
                    break
                follow_msg = steer_q.get_nowait()
                await bus.publish(FollowupStart(group_id=group_id, member_id=bot["id"], message=follow_msg[:200]))
                async with get_db() as db:
                    follow_recent = await get_messages(db, group_id)
                steer_q = _start_steer(group_id, bot["id"])
                follow_ctx = ExecutionContext(
                    bot=bot, group_id=group_id, user_message=follow_msg,
                    sender=sender, history=follow_recent,
                    all_bots=all_bots, all_members=all_members,
                    broadcaster=bus,
                    interaction=interaction.StandardInteraction(),
                    workflow_suffix=wf.system_suffix(group_id),
                    group_name=group_name,
                    group_announcement=group_announcement,
                    steer_channel=steer_q,
                    # follow-up messages don't carry the original file attachment
                )
                try:
                    follow_result = await registry.get(bot.get("executor_id", "simple_v1")).run(follow_ctx)
                finally:
                    _stop_steer(group_id, bot["id"])
                if follow_result.full_text:
                    await wf.check_and_advance(group_id, follow_result.full_text, bot["id"])
            return

        tasks = {asyncio.create_task(call_bot(b)): b for b in bots}
        for b in bots:
            await bus.publish(Typing(group_id=group_id, sender_name=b["name"], avatar_color=b["avatar_color"]))
        done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        try:
            winner_bot, ai_reply, _race_usage = done.pop().result()
        except AIError as e:
            await bus.publish(Error(group_id=group_id, message=str(e)))
            return
        except Exception as e:
            await bus.publish(Error(group_id=group_id, message=f"未知错误：{str(e)}"))
            return
        async with get_db() as db2:
            bot_msg_id = await save_message(db2, group_id, winner_bot["id"], ai_reply,
                                            input_tokens=_race_usage.get("input_tokens") or None,
                                            output_tokens=_race_usage.get("output_tokens") or None,
                                            cache_read_tokens=_race_usage.get("cache_read_tokens") or None,
                                            cache_creation_tokens=_race_usage.get("cache_creation_tokens") or None)
            bot_recent = await get_messages(db2, group_id)
        bg.spawn(add_to_chroma(bot_msg_id, ai_reply, winner_bot["role"] or "", winner_bot["id"]))
        await bus.publish(Message(
            group_id=group_id, id=bot_msg_id,
            member_id=winner_bot["id"], sender_name=winner_bot["name"],
            sender_type="bot", avatar_color=winner_bot["avatar_color"],
            content=ai_reply,
            created_at=bot_recent[-1]["created_at"] if bot_recent else "",
        ))
        bg.spawn(maybe_summarize(group_id, winner_bot["id"], winner_bot["role"] or winner_bot["name"], [winner_bot["id"]]))

    role_groups: dict[str, list] = {}
    for bot in triggered:
        role_groups.setdefault(bot["role"] or bot["name"], []).append(bot)

    for bots in role_groups.values():
        await race_role_group(bots)
        async with get_db() as db_seq:
            ctx['recent'] = await get_messages(db_seq, group_id)

    for bot in triggered:
        await auto_continue_if_needed(group_id, bot, all_members)

    await check_handoff(group_id, all_bots, all_members, recent)

    async with get_db() as db3:
        latest_msgs = await get_messages(db3, group_id, limit=3)
    for msg in reversed(latest_msgs):
        if msg["sender_type"] != "bot":
            continue
        if not any(f"@{m['name']}" in msg["content"] for m in all_members):
            await locks.set_active_bot(group_id, msg["member_id"])
        break

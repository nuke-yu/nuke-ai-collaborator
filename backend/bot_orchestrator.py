import asyncio
import uuid
from database import get_db, get_messages, save_message
from ws_manager import manager
from ai_client import call_ai, call_ai_stream, AIError
from role_router import should_bot_respond, build_context_message
from memory import maybe_summarize, get_memory_context, add_to_chroma
import workflow as wf

# group_id -> bot_id: 记录当前哪个 bot 持有会话
active_bot: dict[int, int] = {}


def select_triggered_bots(content: str, all_bots: list, group_id: int) -> list[dict]:
    """决定本条消息应该由哪些 bot 响应，同时维护 active_bot 锁。"""
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
            active_bot[group_id] = explicit[0]["id"]
        else:
            active_bot.pop(group_id, None)
        return explicit

    locked_bot_id = active_bot.get(group_id)
    if locked_bot_id:
        locked = next((b for b in all_bots if b["id"] == locked_bot_id), None)
        return [locked] if locked else []

    return [b for b in all_bots if should_bot_respond(content, b["name"], b["role"] or "")]


async def stream_bot_response(group_id: int, bot: dict, system_prompt: str,
                               history: list, user_msg: str):
    """流式广播 bot 回复，保存到 DB，返回 (full_text, msg_id) 或 (None, None)"""
    temp_id = str(uuid.uuid4())
    await manager.broadcast(group_id, {
        "type": "stream_start", "temp_id": temp_id,
        "member_id": bot["id"], "sender_name": bot["name"],
        "sender_type": "bot", "avatar_color": bot["avatar_color"],
    })
    provider = bot.get("model_provider", "deepseek")
    model = bot.get("model_name", "deepseek-chat")
    full_text = ""
    try:
        async for chunk in call_ai_stream(system_prompt, history, user_msg, provider, model):
            full_text += chunk
            await manager.broadcast(group_id, {"type": "stream_chunk", "temp_id": temp_id, "delta": chunk})
    except AIError as e:
        await manager.broadcast(group_id, {"type": "stream_error", "temp_id": temp_id, "message": str(e)})
        return None, None

    async with get_db() as db:
        msg_id = await save_message(db, group_id, bot["id"], full_text)
        recent = await get_messages(db, group_id)
    await manager.broadcast(group_id, {
        "type": "stream_end", "temp_id": temp_id, "id": msg_id,
        "member_id": bot["id"], "sender_name": bot["name"],
        "preview": full_text[:100],
        "created_at": recent[-1]["created_at"] if recent else "",
    })
    return full_text, msg_id


async def mark_read(group_id: int, member_id: int, msg_id: int):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO member_read (member_id, group_id, last_read_id) VALUES (?,?,?) "
            "ON CONFLICT(member_id, group_id) DO UPDATE SET last_read_id=excluded.last_read_id",
            (member_id, group_id, msg_id)
        )
        await db.commit()
    await manager.broadcast(group_id, {"type": "read", "member_id": member_id, "last_read_id": msg_id})


async def send_auto_reply(group_id: int, member: dict, reply_to_id: int):
    await asyncio.sleep(1.5)
    async with get_db() as db:
        msg_id = await save_message(db, group_id, member["id"], member["auto_reply"],
                                    reply_to_id=reply_to_id, is_auto_reply=True)
        recent = await get_messages(db, group_id)
        saved = next((m for m in recent if m["id"] == msg_id), {})
    await manager.broadcast(group_id, {"type": "message", **saved})


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
        system_prompt = bot["system_prompt"] or f"你是{bot['name']}，{bot['role']}。"
        memory = await get_memory_context(group_id, bot["role"] or "", "继续代码")
        if memory:
            system_prompt += f"\n\n{memory}"
        ai_reply, bot_msg_id = await stream_bot_response(
            group_id, bot, system_prompt, history,
            "请继续上面未完成的代码，直到所有功能全部实现完毕。完成后在最后一行写上交接信息。"
        )
        if not ai_reply:
            break
        asyncio.create_task(add_to_chroma(bot_msg_id, ai_reply, bot["role"] or "", group_id))
        asyncio.create_task(maybe_summarize(group_id, bot["role"] or bot["name"], [bot["id"]]))
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
            active_bot.pop(group_id, None)
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
    system_prompt = target_bot["system_prompt"] or f"你是{target_bot['name']}，{target_bot['role']}。"
    ai_reply, bot_msg_id = await stream_bot_response(group_id, target_bot, system_prompt, history, handoff_prompt)
    if not ai_reply:
        return
    asyncio.create_task(add_to_chroma(bot_msg_id, ai_reply, target_bot["role"] or "", group_id))
    active_bot[group_id] = target_bot["id"]
    await auto_continue_if_needed(group_id, target_bot, all_members)
    async with get_db() as db_r:
        bot_recent = await get_messages(db_r, group_id)
    await check_handoff(group_id, all_bots, all_members, bot_recent, _depth + 1)


async def dispatch_bots(group_id: int, triggered: list, content: str, sender: dict,
                         recent: list, all_bots: list, all_members: list):
    """执行触发的 bot 列表：竞速、顺序、续写、交接。"""
    ctx = {'recent': recent}

    def _bot_recent(bot):
        cleared = bot.get("context_cleared_at")
        if cleared:
            return [m for m in ctx['recent'] if m.get("created_at", "") > cleared]
        return ctx['recent']

    human_senders = [m for m in recent if m["sender_type"] != "bot"]
    initiator_name = human_senders[0]["sender_name"] if human_senders else sender["name"]

    async def call_bot(bot):
        history, user_msg = build_context_message(content, sender["name"], _bot_recent(bot))
        base_prompt = bot["system_prompt"] or f"你是{bot['name']}，{bot['role']}。"
        memory = await get_memory_context(group_id, bot["role"] or "", content)
        system_prompt = base_prompt + (f"\n\n{memory}" if memory else "")
        if "测试" in (bot["role"] or ""):
            system_prompt += f"\n\n完成测试报告后，在最后一行写：「@{initiator_name} 测试完成，任务全部完成！」"
        ai_reply = await call_ai(system_prompt, history, user_msg)
        return bot, ai_reply

    async def race_role_group(bots):
        if len(bots) == 1:
            bot = bots[0]
            history, user_msg_text = build_context_message(content, sender["name"], _bot_recent(bot))
            base_prompt = bot["system_prompt"] or f"你是{bot['name']}，{bot['role']}。"
            memory = await get_memory_context(group_id, bot["role"] or "", content)
            system_prompt = base_prompt + (f"\n\n{memory}" if memory else "") + wf.system_suffix(group_id)
            ai_reply, bot_msg_id = await stream_bot_response(group_id, bot, system_prompt, history, user_msg_text)
            if not ai_reply:
                return
            asyncio.create_task(add_to_chroma(bot_msg_id, ai_reply, bot["role"] or "", group_id))
            asyncio.create_task(maybe_summarize(group_id, bot["role"] or bot["name"], [bot["id"]]))
            await wf.check_and_advance(group_id, ai_reply, bot["id"])
            return

        tasks = {asyncio.create_task(call_bot(b)): b for b in bots}
        for b in bots:
            await manager.broadcast(group_id, {"type": "typing", "sender_name": b["name"], "avatar_color": b["avatar_color"]})
        done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        try:
            winner_bot, ai_reply = done.pop().result()
        except AIError as e:
            await manager.broadcast(group_id, {"type": "error", "message": str(e)})
            return
        except Exception as e:
            await manager.broadcast(group_id, {"type": "error", "message": f"未知错误：{str(e)}"})
            return
        async with get_db() as db2:
            bot_msg_id = await save_message(db2, group_id, winner_bot["id"], ai_reply)
            bot_recent = await get_messages(db2, group_id)
        asyncio.create_task(add_to_chroma(bot_msg_id, ai_reply, winner_bot["role"] or "", group_id))
        await manager.broadcast(group_id, {
            "type": "message", "id": bot_msg_id,
            "member_id": winner_bot["id"], "sender_name": winner_bot["name"],
            "sender_type": "bot", "avatar_color": winner_bot["avatar_color"],
            "content": ai_reply,
            "created_at": bot_recent[-1]["created_at"] if bot_recent else "",
        })
        asyncio.create_task(maybe_summarize(group_id, winner_bot["role"] or winner_bot["name"], [winner_bot["id"]]))

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
            active_bot[group_id] = msg["member_id"]
        break

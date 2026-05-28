import asyncio
import random
import re
from db import get_db, get_messages, save_message, update_message
from ws_manager import manager

# group_id -> { stages: [stage_dict,...], current: int }
_state: dict[int, dict] = {}


def get(group_id: int) -> dict | None:
    return _state.get(group_id)


def current_bot(group_id: int) -> dict | None:
    """Single-stage active bot, or None if pool/inactive."""
    s = _state.get(group_id)
    if not s or s["current"] >= len(s["stages"]):
        return None
    stage = s["stages"][s["current"]]
    return None if stage.get("stage_type") == "pool" else stage


def current_pool_bots(group_id: int) -> list[int] | None:
    """Bot IDs currently working in a pool stage, None if not a pool stage."""
    s = _state.get(group_id)
    if not s or s["current"] >= len(s["stages"]):
        return None
    stage = s["stages"][s["current"]]
    if stage.get("stage_type") != "pool":
        return None
    in_progress = stage.get("in_progress", {})
    return list(in_progress.keys()) if in_progress else [b["id"] for b in stage["bots"]]


def system_suffix(group_id: int) -> str:
    s = _state.get(group_id)
    if not s:
        return ""
    idx = s["current"]
    total = len(s["stages"])
    stage = s["stages"][idx]
    keyword = stage.get("done_keyword", "完毕")
    next_is_pool = idx + 1 < total and s["stages"][idx + 1].get("stage_type") == "pool"

    if idx + 1 < total:
        next_stage = s["stages"][idx + 1]
        next_name = "开发团队" if next_is_pool else next_stage["name"]
        base = (f"\n\n[工作流 {idx+1}/{total}] 与用户多轮对话，充分澄清所有关键点后，"
                f"当你认为本阶段工作真正完成时，在回复中说出「{keyword}」，"
                f"系统会自动通知 {next_name} 接棒。完成之前请不要说这句话。")
        if next_is_pool:
            dev_count = len(next_stage["bots"])
            base += (
                f"\n\n在说「{keyword}」之前，请用以下格式列出所有需开发的任务：\n\n"
                f"TICKETS:\n1. 任务名称（一句话描述）\n2. 任务名称\n3. 任务名称\n...\n\n"
                f"根据实际工作量拆分，任务数量不限（有 {dev_count} 位开发者会依次认领）。"
            )
        return base
    return (f"\n\n[工作流 {idx+1}/{total}] 这是最后一个阶段。"
            f"完成后说「{keyword}」作为收尾，给出完整的最终结论。")


async def _build_history(group_id: int) -> list[dict]:
    async with get_db() as db:
        recent = await get_messages(db, group_id, limit=20)
    return [
        {"role": "assistant" if m["sender_type"] == "bot" else "user",
         "content": f"[{m['sender_name']}]: {m['content'][:400]}"}
        for m in recent[-12:]
    ]


def _parse_tickets(message: str) -> list[str]:
    match = re.search(r'TICKETS:\s*\n((?:(?:\d+\.|[-*+])\s+.+\n?)+)', message, re.IGNORECASE)
    if match:
        items = re.findall(r'(?:\d+\.|[-*+])\s+(.+)', match.group(1))
        if items:
            return [t.strip() for t in items[:20]]
    items = re.findall(r'^\s*(?:\d+\.|[-*+])\s+(.+)$', message, re.MULTILINE)
    if len(items) >= 2:
        return [t.strip() for t in items[:20]]
    return ["本次迭代任务"]


async def check_and_advance(group_id: int, response: str, bot_id: int = None) -> bool:
    s = _state.get(group_id)
    if not s:
        return False
    stage = s["stages"][s["current"]]

    if stage.get("stage_type") == "pool":
        keyword = stage.get("done_keyword", "")
        if not (keyword and keyword in response):
            return False
        in_progress = stage.get("in_progress", {})
        if bot_id is None or bot_id not in in_progress:
            return False

        # Complete current ticket
        done_ticket = in_progress.pop(bot_id)
        stage.setdefault("completed_tickets", []).append(done_ticket)
        queue = stage.get("ticket_queue", [])
        bot_dict = next((b for b in stage["bots"] if b["id"] == bot_id), None)

        if queue:
            next_ticket = queue.pop(0)
            in_progress[bot_id] = next_ticket
            await broadcast_state(group_id)
            if bot_dict:
                ann = f"✅ **{bot_dict['name']}** 完成「{done_ticket}」，认领下一个：「{next_ticket}」"
                await _post_system_msg(group_id, bot_dict, ann)
                asyncio.create_task(_trigger_pool_bot(group_id, bot_dict, next_ticket, stage))
            return False

        # No more tickets — bot goes idle
        stage.setdefault("idle_bots", []).append(bot_id)
        if bot_dict:
            ann = f"✅ **{bot_dict['name']}** 完成「{done_ticket}」，等待其他任务"
            await _post_system_msg(group_id, bot_dict, ann)
        await broadcast_state(group_id)

        if not in_progress:
            await advance(group_id)
            return True
        return False

    keyword = stage.get("done_keyword", "")
    if keyword and keyword in response:
        await advance(group_id)
        return True
    return False


async def _post_system_msg(group_id: int, sender_bot: dict, text: str):
    async with get_db() as db:
        ann_id = await save_message(db, group_id, sender_bot["id"], text)
        recent = await get_messages(db, group_id, limit=3)
    saved = next((m for m in recent if m["id"] == ann_id), {})
    await manager.broadcast(group_id, {
        "type": "message", **saved,
        "sender_name": "工作流系统", "avatar_color": "#6366f1",
    })


def start(group_id: int, ordered_stages: list):
    _state[group_id] = {"stages": ordered_stages, "current": 0}


def end(group_id: int):
    _state.pop(group_id, None)


def _snapshot(group_id: int) -> dict:
    s = _state.get(group_id)
    if not s:
        return {"active": False}
    stages_out = []
    for stage in s["stages"]:
        if stage.get("stage_type") == "pool":
            in_prog = stage.get("in_progress", {})
            done_tickets = stage.get("completed_tickets", [])
            queue = stage.get("ticket_queue", [])
            stages_out.append({
                "stage_type": "pool",
                "bots": [{"id": b["id"], "name": b["name"], "avatar_color": b["avatar_color"]}
                         for b in stage["bots"]],
                "in_progress": {str(k): v for k, v in in_prog.items()},
                "completed_count": len(done_tickets),
                "total_tickets": len(done_tickets) + len(in_prog) + len(queue),
                "done_keyword": stage.get("done_keyword", ""),
            })
        else:
            stages_out.append({
                "stage_type": "single",
                "id": stage["id"], "name": stage["name"],
                "avatar_color": stage["avatar_color"], "role": stage.get("role") or "",
                "done_keyword": stage.get("done_keyword", ""),
            })
    return {"active": True, "stages": stages_out, "current": s["current"]}


async def broadcast_state(group_id: int):
    await manager.broadcast(group_id, {"type": "workflow_update", **_snapshot(group_id)})


async def advance(group_id: int) -> bool:
    s = _state.get(group_id)
    if not s:
        return True
    prev_stage = s["stages"][s["current"]]
    s["current"] += 1
    if s["current"] >= len(s["stages"]):
        end(group_id)
        await manager.broadcast(group_id, {"type": "workflow_update", "active": False, "done": True})
        return True
    await broadcast_state(group_id)
    asyncio.create_task(_trigger_next(group_id, prev_stage, s["stages"][s["current"]]))
    return False


async def _trigger_next(group_id: int, prev_stage: dict, next_stage: dict):
    if next_stage.get("stage_type") == "pool":
        await _trigger_pool_stage(group_id, prev_stage, next_stage)
    else:
        await _trigger_single_stage(group_id, prev_stage, next_stage)


async def _trigger_single_stage(group_id: int, prev_stage: dict, next_bot: dict):
    from ai.client import call_ai_stream, AIError
    await asyncio.sleep(0.5)
    history = await _build_history(group_id)
    trigger_msg = f"请开始你（{next_bot['name']} · {next_bot.get('role', '')}）的工作。"
    base = next_bot["system_prompt"] or f"你是{next_bot['name']}，{next_bot.get('role', '')}。"
    p = (next_bot.get("personality_prompt") or "").strip()
    system_prompt = (base + f"\n\n【性格指令】\n{p}" if p else base) + system_suffix(group_id)

    async with get_db() as db:
        msg_id = await save_message(db, group_id, next_bot["id"], "...")
    await manager.broadcast(group_id, {
        "type": "stream_start", "temp_id": f"wf_{msg_id}",
        "member_id": next_bot["id"], "sender_name": next_bot["name"],
        "sender_type": "bot", "avatar_color": next_bot["avatar_color"],
    })
    full = ""
    try:
        provider = next_bot.get("model_provider", "deepseek")
        model = next_bot.get("model_name", "deepseek-chat")
        temperature = next_bot.get("temperature", 0.7)
        max_tokens = next_bot.get("max_tokens", 4096)
        async for chunk in call_ai_stream(system_prompt, history, trigger_msg, provider, model, temperature, max_tokens):
            full += chunk
            await manager.broadcast(group_id, {"type": "stream_chunk", "temp_id": f"wf_{msg_id}", "delta": chunk})
    except AIError as e:
        full = f"[错误] {e}"

    async with get_db() as db:
        await update_message(db, msg_id, full)
        recent2 = await get_messages(db, group_id, limit=5)
    saved = next((m for m in recent2 if m["id"] == msg_id), {})
    await manager.broadcast(group_id, {"type": "stream_end", "temp_id": f"wf_{msg_id}", **saved})
    await check_and_advance(group_id, full, next_bot["id"])


async def _trigger_pool_stage(group_id: int, prev_stage: dict, pool_stage: dict):
    await asyncio.sleep(0.5)

    async with get_db() as db:
        recent = await get_messages(db, group_id, limit=10)

    prev_id = prev_stage.get("id") if prev_stage.get("stage_type", "single") != "pool" else None
    prev_msgs = [m for m in recent if prev_id and m.get("member_id") == prev_id]
    last_content = prev_msgs[-1]["content"] if prev_msgs else (recent[-1]["content"] if recent else "")
    tickets = _parse_tickets(last_content)

    bots = list(pool_stage["bots"])
    random.shuffle(bots)

    # Initial assignment: one ticket per bot (queue holds the rest)
    initial_assign = min(len(bots), len(tickets))
    pool_stage["ticket_queue"] = list(tickets[initial_assign:])
    pool_stage["in_progress"] = {bots[i]["id"]: tickets[i] for i in range(initial_assign)}
    pool_stage["completed_tickets"] = []
    pool_stage["idle_bots"] = []

    lines = [f"🎯 共 {len(tickets)} 个开发任务，{len(bots)} 位开发者开始认领！"]
    for i in range(initial_assign):
        lines.append(f"✅ **{bots[i]['name']}** 认领了「{tickets[i]}」")
    for bot in bots[initial_assign:]:
        lines.append(f"⏳ **{bot['name']}** 等待认领")
    announcement = "\n".join(lines)

    first_bot = bots[0]
    async with get_db() as db:
        ann_id = await save_message(db, group_id, first_bot["id"], announcement)
        recent_ann = await get_messages(db, group_id, limit=3)
    saved_ann = next((m for m in recent_ann if m["id"] == ann_id), {})
    await manager.broadcast(group_id, {
        "type": "message", **saved_ann,
        "sender_name": "工作流系统", "avatar_color": "#6366f1",
    })
    await broadcast_state(group_id)

    for i in range(initial_assign):
        asyncio.create_task(_trigger_pool_bot(group_id, bots[i], tickets[i], pool_stage))


async def _trigger_pool_bot(group_id: int, bot: dict, ticket: str, pool_stage: dict):
    from ai.client import call_ai_stream, AIError
    history = await _build_history(group_id)

    s = _state.get(group_id)
    if not s:
        return
    idx = s["current"]
    total = len(s["stages"])
    keyword = pool_stage.get("done_keyword", "完毕")
    base = bot["system_prompt"] or f"你是{bot['name']}，{bot.get('role', '')}。"
    p = (bot.get("personality_prompt") or "").strip()
    system_prompt = (base + f"\n\n【性格指令】\n{p}" if p else base) + (
        f"\n\n[工作流 {idx+1}/{total}] 你当前认领的任务：「{ticket}」\n"
        f"请完成这个任务，描述你的实现方案、关键代码思路，并给出 commit message 和 PR 描述。\n"
        f"完成后在回复末尾说「{keyword}」，系统会自动为你分配下一个任务（如果有的话）。"
    )
    trigger_msg = f"开始开发任务：{ticket}"

    async with get_db() as db:
        msg_id = await save_message(db, group_id, bot["id"], "...")
    await manager.broadcast(group_id, {
        "type": "stream_start", "temp_id": f"pool_{msg_id}",
        "member_id": bot["id"], "sender_name": bot["name"],
        "sender_type": "bot", "avatar_color": bot["avatar_color"],
    })
    full = ""
    try:
        provider = bot.get("model_provider", "deepseek")
        model = bot.get("model_name", "deepseek-chat")
        temperature = bot.get("temperature", 0.7)
        max_tokens = bot.get("max_tokens", 4096)
        async for chunk in call_ai_stream(system_prompt, history, trigger_msg, provider, model, temperature, max_tokens):
            full += chunk
            await manager.broadcast(group_id, {
                "type": "stream_chunk", "temp_id": f"pool_{msg_id}", "delta": chunk
            })
    except AIError as e:
        full = f"[错误] {e}"

    async with get_db() as db:
        await update_message(db, msg_id, full)
        recent2 = await get_messages(db, group_id, limit=5)
    saved = next((m for m in recent2 if m["id"] == msg_id), {})
    await manager.broadcast(group_id, {"type": "stream_end", "temp_id": f"pool_{msg_id}", **saved})
    await check_and_advance(group_id, full, bot["id"])

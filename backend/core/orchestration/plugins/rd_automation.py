import asyncio
import json
import logging
from core.orchestration.base import OrchestratorStep, WorkUnit
from core import bg
import random
import core.workflow as wf
from bus import bus
from bus.events import TicketCreated, CodeCommitted
from db import global_db, connect, get_messages

log = logging.getLogger(__name__)

async def _on_ticket_created(ev: dict):
    group_id = ev["group_id"]
    ticket_id = ev["ticket_id"]
    title = ev["title"]
    
    async with global_db() as db:
        async with db.execute("SELECT name, announcement FROM groups WHERE id = ?", (group_id,)) as cur:
            group_info = await cur.fetchone()
        async with db.execute("SELECT * FROM members WHERE group_id = ?", (group_id,)) as cur:
            # Note: This is a simplification of get_members
            columns = [column[0] for column in cur.description]
            all_members = [dict(zip(columns, row)) for row in await cur.fetchall()]
            
    all_bots = [m for m in all_members if m["type"] == "bot"]
    dev_bots = [b for b in all_bots if "dev" in (b.get("role") or "").lower() or "developer" in (b.get("role") or "").lower()]
    
    if not dev_bots:
        return

    # Expertise Match
    scored_bots = []
    search_text = title.lower()
    for bot in dev_bots:
        score = 0
        role = bot.get("role") or ""
        traits = bot.get("traits") or "[]"

        traits_list = json.loads(traits) if isinstance(traits, str) else traits
        keywords = [role.lower()] + [t.lower() for t in traits_list if isinstance(t, str)]
        for kw in keywords:
            if kw and kw in search_text:
                score += 10
        score += random.random()
        scored_bots.append((score, bot))
    
    scored_bots.sort(key=lambda x: x[0], reverse=True)
    target_bot = scored_bots[0][1]
    
    content = f"[系统通知] 发现新任务：{title} ({ticket_id})。该任务已分配给你。请认领并开始执行。"
    msg_dict = {"group_id": group_id, "content": content, "member_id": 0, "sender_type": "system"}
    
    async with connect() as db_m:
        recent = await get_messages(db_m, group_id)
        
    orch = wf._orch_for(group_id)
    # Force trigger this specific bot

    step = OrchestratorStep(next_units=[
        WorkUnit(bot=target_bot, group_id=group_id, message=msg_dict, history=recent, all_bots=all_bots, all_members=all_members)
    ])
    

    bg.spawn_group(group_id, wf.apply(group_id, step))

async def _on_code_committed(ev: dict):
    group_id = ev["group_id"]
    files = ev["files"]
    ticket_id = ev["ticket_id"]
    
    async with global_db() as db:
        async with db.execute("SELECT * FROM members WHERE group_id = ?", (group_id,)) as cur:
            columns = [column[0] for column in cur.description]
            all_members = [dict(zip(columns, row)) for row in await cur.fetchall()]

    all_bots = [m for m in all_members if m["type"] == "bot"]
    qa_bots = [b for b in all_bots if "qa" in (b.get("role") or "").lower()]
    if not qa_bots: return

    target_bot = qa_bots[0]
    file_list = ", ".join(files)
    content = f"[系统通知] 发现代码提交：{file_list}。请针对 Ticket {ticket_id} 执行本地测试流水线并反馈结果。"
    msg_dict = {"group_id": group_id, "content": content, "member_id": 0, "sender_type": "system"}
    
    async with connect() as db_m:
        recent = await get_messages(db_m, group_id)
        
    orch = wf._orch_for(group_id)

    step = OrchestratorStep(next_units=[
        WorkUnit(bot=target_bot, group_id=group_id, message=msg_dict, history=recent, all_bots=all_bots, all_members=all_members)
    ])
    

    bg.spawn_group(group_id, wf.apply(group_id, step))

async def start():
    log.info("RDAutomation Plugin starting...")
    sub_tc = bus.subscribe(TicketCreated)
    sub_cc = bus.subscribe(CodeCommitted)
    
    async def loop_tc():
        async with sub_tc:
            async for ev in sub_tc:
                try: await _on_ticket_created(ev)
                except Exception: log.exception("RDAutomation: TC failed")

    async def loop_cc():
        async with sub_cc:
            async for ev in sub_cc:
                try: await _on_code_committed(ev)
                except Exception: log.exception("RDAutomation: CC failed")

    asyncio.gather(loop_tc(), loop_cc())

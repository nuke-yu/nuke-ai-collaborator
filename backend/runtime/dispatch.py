"""CELL-14: worker-side real dispatch for a downstream user_message.

Split-aware port of main.py's websocket message block: CENTRAL reads (sender /
members / group info) go to the central DB via db.global_db(); the message itself
and everything downstream (recent history, session, locks, workflow) live in the
group's private DB, which the Worker has already bound (db.bind_db) before calling
this. The actual bot run is fire-and-forget via bg.spawn_group so the worker's
downstream loop stays responsive (abort can still interrupt it); its bus events
flow out through the Worker's upstream pump.
"""
import dataclasses
import logging
import re

import db
from bus import bus
from bus.events import Message

log = logging.getLogger(__name__)

_SYSTEM_SENDER = {"id": 0, "name": "系统调度器", "type": "system", "avatar_color": "#6b7280"}


# get_messages() rows carry extra display keys (avatar_color, reply_to, cost_usd,
# …) that the Message event doesn't declare; spread only the fields it accepts.
_MESSAGE_FIELDS = {f.name for f in dataclasses.fields(Message)}


async def dispatch_user_message(msg: dict) -> None:
    gid = msg["group_id"]
    sender_id = msg.get("member_id")
    content = (msg.get("content") or "").strip()
    file_url = msg.get("file_url")
    file_name = msg.get("file_name")
    file_size = msg.get("file_size")
    file_type = msg.get("file_type")
    online_ids = set(msg.get("online_ids", []))
    if not content and not file_url:
        return

    # ── central domain (sender / members / group) ──
    async with db.global_db() as cdb:
        sender = await db.get_member(cdb, sender_id)
        if not sender:
            return
        all_members = await db.get_members(cdb, gid)
        group_info = await db.get_group(cdb, gid) or {}
    all_bots = [m for m in all_members if m["type"] == "bot"]

    # ── group domain (persist message + load recent) ──
    async with db.write_connect() as gdb:        # bound -> group's private DB
        msg_id = await db.save_message(
            gdb, gid, sender_id, content,
            msg.get("reply_to_id"), file_url, file_name, file_size, file_type,
        )
        recent = await db.get_messages(gdb, gid)
        saved = next((m for m in recent if m["id"] == msg_id), {})

    # echo the user's own message back out (Supervisor fans it to the group)
    await bus.publish(Message(group_id=gid, **{
        k: v for k, v in saved.items() if k in _MESSAGE_FIELDS and k != "group_id"
    }))

    import core.workflow as wf
    from core.orchestration.interaction import StandardInteraction
    from core import bg
    
    # ── Orchestration (V3 Unified) ──
    interaction = StandardInteraction()
    await interaction.mark_read(gid, sender_id, msg_id)

    # ── Auto-reply logic ──
    mentioned_names = set(re.findall(r'@(\S+)', content or ""))
    if mentioned_names:
        for m in all_members:
            if m["name"] in mentioned_names and m["id"] not in online_ids and m.get("auto_reply"):
                bg.spawn(interaction.send_auto_reply(gid, m, msg_id))

    # Unified Orchestration Call
    orch = wf._orch_for(gid)
    step = await orch.dispatch(gid, saved, all_members, recent)
    
    if step.next_units:
        # Side-effects (broadcast typing, AI run, etc.) are handled by the runner
        bg.spawn_group(gid, wf.apply(gid, step))


async def dispatch_wake_trigger(msg: dict) -> None:
    gid = msg["group_id"]
    bot_id = msg.get("bot_id")
    content = msg.get("content", "")
    if not bot_id:
        return

    # ── central domain (members / group) ──
    async with db.global_db() as cdb:
        all_members = await db.get_members(cdb, gid)
        group_info = await db.get_group(cdb, gid) or {}
    all_bots = [m for m in all_members if m["type"] == "bot"]
    bot = next((b for b in all_bots if b["id"] == bot_id), None)
    if not bot:
        return

    # ── group domain (load recent) ──
    async with db.connect() as gdb:
        recent = await db.get_messages(gdb, gid)

    import core.workflow as wf
    from core import bg
    
    # Unified Orchestration Call
    orch = wf._orch_for(gid)
    # Wrap system message in a dict for dispatch compatibility
    msg_dict = {"group_id": gid, "content": content, "member_id": 0, "sender_type": "system"}
    step = await orch.dispatch(gid, msg_dict, all_members, recent)
    
    if step.next_units:
        bg.spawn_group(gid, wf.apply(gid, step))

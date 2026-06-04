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


async def dispatch_start_workflow(msg: dict) -> None:
    """起 RD 人确认流水线（BA→Dev→QA 三道门）。在 worker 进程里 start，编排状态就落在
    处理本群消息的同一个 worker 上（并经 workflow_store 持久化），首阶段(BA)由用户
    驱动，故不立即派发 bot。角色按 role_router 家族匹配；缺角色则广播提示、不启动。"""
    gid = msg["group_id"]
    from core.role_router import _role_family
    from core.orchestration.pipeline import build_rd_pipeline
    import core.workflow as wf

    async with db.global_db() as cdb:
        all_members = await db.get_members(cdb, gid)
    all_bots = [m for m in all_members if m["type"] == "bot"]

    picked: dict = {}
    for b in all_bots:
        fam = _role_family(b.get("role") or "")
        if fam in ("ba", "dev", "qa") and fam not in picked:
            picked[fam] = b

    label = {"ba": "BA(需求)", "dev": "Dev(开发)", "qa": "QA(测试)"}
    missing = [r for r in ("ba", "dev", "qa") if r not in picked]
    if missing:
        await bus.broadcast(gid, {
            "type": "message", "member_id": 0, "sender_name": "工作流系统",
            "avatar_color": "#6366f1",
            "content": "无法开始需求流程：群里缺少角色 —— "
                       + "、".join(label[m] for m in missing)
                       + "。请确保群内 BA / Dev / QA 各有一个 bot（按 role 识别）。",
        })
        return

    stages = build_rd_pipeline(picked["ba"], picked["dev"], picked["qa"])
    await wf.apply(gid, wf.start(gid, stages, "workflow_v1"))
    await bus.broadcast(gid, {
        "type": "message", "member_id": 0, "sender_name": "工作流系统",
        "avatar_color": "#6366f1",
        "content": (f"🚀 需求流程已开始（{picked['ba']['name']} → {picked['dev']['name']} "
                    f"→ {picked['qa']['name']}）。请向 {picked['ba']['name']} 描述你的需求，"
                    f"TA 会和你逐步澄清；每完成一步会有一张确认卡片等你点确认。"),
    })


async def dispatch_workflow_next(msg: dict) -> None:
    """手动推进工作流到下一阶段。必须在 worker 进程执行：编排器活内存状态在这里，
    主进程的 REST 端点只负责把本控制帧转发过来（同 confirm 路径）。"""
    import core.workflow as wf
    await wf.advance(msg["group_id"])


async def dispatch_workflow_end(msg: dict) -> None:
    """结束工作流：丢弃 worker 内存编排状态、清持久化快照、广播 workflow_update。
    REST 的 DELETE /workflow 仅转发本帧（旧实现在主进程操作空内存 + 写错库）。"""
    gid = msg["group_id"]
    import core.workflow as wf
    from core import workflow_store
    wf.end(gid)
    await workflow_store.clear_state(gid)
    await bus.broadcast(gid, {"type": "workflow_update", "active": False})


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

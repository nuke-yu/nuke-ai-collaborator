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


def _coalesce_should_abort(sender: dict, next_units: list) -> bool:
    """Coalesce-latest (B): should a new message abort the group's in-progress run?

    Yes only when a HUMAN sends a free-form message — so a rapid follow-up makes
    the bot answer the latest intent instead of stacking replies. We never tear
    down an active workflow stage (is_workflow units) and never let bot/system
    messages interrupt a run.
    """
    if not next_units:
        return False
    if (sender or {}).get("type") != "human":
        return False
    if any(getattr(u, "is_workflow", False) for u in next_units):
        return False
    return True


async def dispatch_user_message(msg: dict) -> None:
    gid = msg["group_id"]
    sender_id = msg.get("member_id")
    content = (msg.get("content") or "").strip()
    channel_meta = msg.get("channel_meta")
    channel_attachments = (
        channel_meta.get("attachments")
        if isinstance(channel_meta, dict) and isinstance(channel_meta.get("attachments"), list)
        else []
    )
    if not content and channel_attachments:
        kinds = [
            str(item.get("type") or "file")
            for item in channel_attachments
            if isinstance(item, dict)
        ]
        content = f"[收到外部渠道附件: {', '.join(kinds) or 'file'}]"
    from core import media
    file_url = media.canonicalize(msg.get("file_url"))  # persist stable ref, never a signed URL
    file_name = msg.get("file_name")
    file_size = msg.get("file_size")
    file_type = msg.get("file_type")
    online_ids = set(msg.get("online_ids", []))
    if not content and not file_url:
        return

    # ── central domain (sender / members / group) ──
    async with db.global_db() as cdb:
        channel_sender = msg.get("channel_sender")
        sender = dict(channel_sender) if isinstance(channel_sender, dict) else await db.get_member(cdb, sender_id)
        if not sender:
            return
        all_members = await db.get_members(cdb, gid)
        group_info = await db.get_group(cdb, gid) or {}
    if sender.get("type") == "integration":
        all_members = [*all_members, sender]
    all_bots = [m for m in all_members if m["type"] == "bot"]

    # ── group domain (persist message + load recent) ──
    async with db.write_connect() as gdb:        # bound -> group's private DB
        msg_id, created = await db.save_message(
            gdb, gid, sender_id, content,
            msg.get("reply_to_id"), file_url, file_name, file_size, file_type,
            meta=channel_meta,
            sender_snapshot=sender if sender.get("type") == "integration" else None,
            external_message_key=msg.get("channel_message_key"),
            return_created=True,
        )
        if not created:
            return
        recent = await db.get_messages(gdb, gid)
    saved = next((m for m in recent if m["id"] == msg_id), {})
    if msg.get("channel_target_bot_id"):
        saved["target_bot_id"] = int(msg["channel_target_bot_id"])

    # echo the user's own message back out (Supervisor fans it to the group)
    media.presign_message(saved)  # canonical in DB → signed URL on the wire
    await bus.publish(Message(group_id=gid, **{
        k: v for k, v in saved.items() if k in _MESSAGE_FIELDS and k != "group_id"
    }))

    import core.workflow as wf
    from core.orchestration.interaction import StandardInteraction
    from core import bg
    
    # ── Orchestration (V3 Unified) ──
    interaction = StandardInteraction()
    await interaction.mark_read(gid, sender_id, msg_id)

    # ── Clear Away Recap if sender is human ──
    if sender.get("type") == "human":
        from core.recap import clear_recap
        bg.spawn(clear_recap(gid))

    # ── Auto-reply logic ──
    mentioned_names = set(re.findall(r'@(\S+)', content or ""))
    if mentioned_names:
        for m in all_members:
            if m["name"] in mentioned_names and m["id"] not in online_ids and m.get("auto_reply"):
                bg.spawn(interaction.send_auto_reply(gid, m, msg_id))

    # Unified Orchestration Call
    orch = wf._orch_for(gid)
    step = await orch.dispatch(gid, saved, all_members, recent)
    personal_user_id = int(msg.get("user_id") or 0)
    if personal_user_id and sender.get("type") == "human":
        for unit in step.next_units:
            unit.tag["personal_user_id"] = personal_user_id

    # If user's UI is set to English, ask each bot to reply in English.
    if msg.get("lang") == "en" and step.next_units:
        lang_hint = ("\n\n[LANGUAGE: The user's interface language is English. "
                     "Please think and respond ENTIRELY in English. Do not use Chinese.]")
        for unit in step.next_units:
            unit.prompt_suffix = (unit.prompt_suffix or "") + lang_hint

    if step.next_units:
        # B (coalesce-latest): a human free-form follow-up aborts the group's
        # in-progress run first, so the bot answers the LATEST message instead of
        # running overlapping/stacked replies. The per-group run lock (A) then
        # sequences the clean handoff: the cancelled run releases the lock before
        # the new run acquires it and reloads fresh history (incl. this message).
        if _coalesce_should_abort(sender, step.next_units):
            bg.abort_group(gid)
        # Side-effects (broadcast typing, AI run, etc.) are handled by the runner
        bg.spawn_group(gid, wf.apply(gid, step))


async def dispatch_start_workflow(msg: dict) -> None:
    """在 worker 进程里 start 工作流。编排状态和派发的 background task 都跑在 Worker 进程上。"""
    gid = msg["group_id"]
    body = msg.get("body")
    import core.workflow as wf

    if body:
        orchestrator_id = body.get("orchestrator_id", "workflow_v1")
        async with db.global_db() as cdb:
            members = await db.get_members(cdb, gid)
        bots = {m["id"]: m for m in members if m["type"] == "bot"}

        from core.orchestration import registry as orch_registry
        orch = orch_registry.get(orchestrator_id)

        stages_cfg = body.get("stages", [])
        if orchestrator_id == "workflow_v1" and stages_cfg:
            from core.role_router import _role_family
            from core.orchestration.pipeline import build_ba_stage, build_dev_stage, build_qa_stage
            
            resolved_bots = []
            for cfg in stages_cfg:
                bot_id = cfg.get("bot_id")
                bot = bots.get(bot_id)
                if bot:
                    resolved_bots.append((bot, cfg))
            
            stages = []
            for bot, cfg in resolved_bots:
                role_fam = _role_family(bot.get("role") or "")
                if role_fam == "ba":
                    stage = build_ba_stage(bot)
                elif role_fam == "dev":
                    stage = build_dev_stage(bot)
                elif role_fam == "qa":
                    dev_idx = next((i for i, s in enumerate(stages) if _role_family(s.get("role") or "") == "dev"), 1)
                    stage = build_qa_stage(bot, rework_to=dev_idx)
                else:
                    stage = {**bot, "stage_type": "single", "gate": True}
                
                if "done_keyword" in cfg:
                    stage["done_keyword"] = cfg["done_keyword"]
                stages.append(stage)
                
            spec = {"stages": stages}
        else:
            spec = orch.parse_spec(body, bots)

        valid = spec.get("bots") or spec.get("stages")
        if not valid:
            sys_name = "Workflow System" if msg.get("lang") == "en" else "工作流系统"
            content = "No bots specified for workflow" if msg.get("lang") == "en" else "未指定工作流的 Bot"
            await bus.broadcast(gid, {
                "type": "message", "member_id": 0, "sender_name": sys_name,
                "avatar_color": "#6366f1", "content": content,
            })
            return
        await wf.apply(gid, wf.start(gid, spec, orchestrator_id))
        return

    # 默认起 RD 人确认流水线（BA→Dev→QA 三道门）
    from core.role_router import _role_family
    from core.orchestration.pipeline import build_rd_pipeline

    async with db.global_db() as cdb:
        all_members = await db.get_members(cdb, gid)
    all_bots = [m for m in all_members if m["type"] == "bot"]

    picked: dict = {}
    for b in all_bots:
        fam = _role_family(b.get("role") or "")
        if fam in ("ba", "dev", "qa") and fam not in picked:
            picked[fam] = b

    en = msg.get("lang") == "en"
    label = ({"ba": "BA(Requirements)", "dev": "Dev", "qa": "QA"}
             if en else {"ba": "BA(需求)", "dev": "Dev(开发)", "qa": "QA(测试)"})
    sys_name = "Workflow System" if en else "工作流系统"
    missing = [r for r in ("ba", "dev", "qa") if r not in picked]
    if missing:
        missing_labels = (", ".join(label[m] for m in missing) if en
                          else "、".join(label[m] for m in missing))
        content = (f"Cannot start pipeline: missing roles — {missing_labels}. "
                   f"Please ensure the group has BA / Dev / QA bots (matched by role)."
                   if en else
                   f"无法开始需求流程：群里缺少角色 —— {missing_labels}"
                   f"。请确保群内 BA / Dev / QA 各有一个 bot（按 role 识别）。")
        await bus.broadcast(gid, {
            "type": "message", "member_id": 0, "sender_name": sys_name,
            "avatar_color": "#6366f1", "content": content,
        })
        return

    stages = build_rd_pipeline(picked["ba"], picked["dev"], picked["qa"])
    await wf.apply(gid, wf.start(gid, {"stages": stages}, "workflow_v1"))
    ba, dev, qa = picked["ba"]["name"], picked["dev"]["name"], picked["qa"]["name"]
    content = (f"🚀 Pipeline started ({ba} → {dev} → {qa}). "
               f"Describe your requirements to {ba} — TA will clarify step by step. "
               f"A confirmation card will appear after each stage."
               if en else
               f"🚀 需求流程已开始（{ba} → {dev} → {qa}）。请向 {ba} 描述你的需求，"
               f"TA 会和你逐步澄清；每完成一步会有一张确认卡片等你点确认。")
    await bus.broadcast(gid, {
        "type": "message", "member_id": 0, "sender_name": sys_name,
        "avatar_color": "#6366f1", "content": content,
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

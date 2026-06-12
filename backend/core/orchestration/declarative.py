"""
core/orchestration/declarative.py — 数据驱动编排器（内置默认）

把"编排即数据"落地：阶段定义就是 begin() 传进来的 ordered_stages 列表，
本类是一个通用解释器 / 纯派发器 —— 它只管"推进到第几个阶段、调谁的 handler"，
具体某个 stage_type（single / pool / …）怎么进入 / 流转 / 序列化，全由
core.orchestration.stages 里按名注册的 StageType handler 决定。

所有方法都是纯决策：只读写内部 _state，返回 OrchestratorStep，绝不发事件 / 调 AI。
加一个新阶段类型不需要改本文件，只需注册一个 StageType。
"""
import logging
from core.orchestration.base import Orchestrator, OrchestratorStep, WorkUnit
from core.orchestration import locks
from core.orchestration.stages import StageCtx, stage_handler


class DeclarativeOrchestrator(Orchestrator):
    orchestrator_id = "workflow_v1"

    def __init__(self) -> None:
        # group_id -> { stages: [...], current: int }
        self._state: dict[int, dict] = {}

    # ── 上下文构造 ──────────────────────────────────────────────────────────────

    def _ctx(self, group_id: int, idx: int | None = None) -> StageCtx | None:
        s = self._state.get(group_id)
        if not s:
            return None
        if idx is None:
            idx = s["current"]
        if idx < 0 or idx >= len(s["stages"]):
            return None
        return StageCtx(orch=self, group_id=group_id, stage=s["stages"][idx],
                        idx=idx, total=len(s["stages"]))

    # ── 状态查询（runner / core.orchestrator 用） ──────────────────────────────

    def get(self, group_id: int) -> dict | None:
        return self._state.get(group_id)

    def current_bot(self, group_id: int) -> dict | None:
        ctx = self._ctx(group_id)
        return None if ctx is None else stage_handler(ctx.stage).current_bot(ctx.stage)

    def current_pool_bots(self, group_id: int) -> list[int] | None:
        ctx = self._ctx(group_id)
        return None if ctx is None else stage_handler(ctx.stage).current_pool_bots(ctx.stage)

    def current_stage_role(self, group_id: int) -> str | None:
        ctx = self._ctx(group_id)
        if ctx and ctx.stage:
            role = ctx.stage.get("role") or ""
            from core.role_router import _role_family
            family = _role_family(role)
            return family if family else ctx.stage.get("name")
        return None

    def is_awaiting_confirm(self, group_id: int) -> bool:
        s = self._state.get(group_id)
        return bool(s.get("awaiting_confirm")) if s else False

    def system_suffix(self, group_id: int) -> str:
        ctx = self._ctx(group_id)
        if ctx is None:
            return ""
        idx, total = ctx.idx, ctx.total
        keyword = ctx.stage.get("done_keyword", "完毕")

        # 首阶段(idx==0)由用户驱动、begin 不派发 enter，本阶段任务指令(instruction)
        # 不会经 trigger 送达，只能随 system_suffix 一起带给 bot；后续阶段的 instruction
        # 已由 enter 的 trigger 送达，这里不再重复。
        brief = ""
        if idx == 0 and ctx.stage.get("instruction"):
            brief = f"\n\n你这一阶段的任务：{ctx.stage['instruction']}"

        if idx + 1 < total:
            nxt = self._ctx(group_id, idx + 1)
            handler = stage_handler(nxt.stage)
            base = (f"\n\n[工作流 {idx+1}/{total}] 与用户多轮对话，充分澄清所有关键点后，"
                    f"当你认为本阶段工作真正完成时，在回复的最后一行单独、原样输出标记 {keyword}"
                    f"（一字不差，不要翻译/改写/加别的字）——系统识别到这个标记才会弹出确认卡片，"
                    f"人点确认后才会通知 {handler.display_name(nxt.stage)} 接棒。完成之前绝对不要输出它。")
            return brief + base + handler.incoming_requirement(nxt.stage, keyword)
        return brief + (f"\n\n[工作流 {idx+1}/{total}] 这是最后一个阶段。完成后在回复的最后一行"
                        f"单独、原样输出标记 {keyword}（一字不差）作为收尾，并给出完整的最终结论。")

    def snapshot(self, group_id: int) -> dict:
        return self.snapshot_state(self._state.get(group_id))

    def snapshot_state(self, state: dict | None) -> dict:
        """从一个 state blob 渲染前端快照（纯函数，不读写 self._state）。

        编排实际跑在 worker 进程，其内存 _state 才是活的；但 REST 应用跑在主进程、
        内存恒空，只能从 group 私有库读出的持久化 blob 来渲染。snapshot() 即为本函数
        在 live 内存态上的特例。blob 经 JSON round-trip 后 stage 内 int key 会变 str，
        故先 rehydrate（各 stage_type 的 rehydrate 均幂等，对 live 态调用无副作用）。"""
        if not state:
            return {"active": False}
        stages = state.get("stages", [])
        for st in stages:
            stage_handler(st).rehydrate(st)
        pending = state.get("awaiting_confirm")
        return {
            "active": True,
            "stages": [stage_handler(st).snapshot(st) for st in stages],
            "current": state.get("current", 0),
            "awaiting_confirm": pending["gate_id"] if pending else None,
        }

    # ── 持久化 / 崩溃恢复 ───────────────────────────────────────────────────────

    def serialize(self, group_id: int) -> dict | None:
        return self._state.get(group_id)

    def restore(self, group_id: int, state: dict) -> None:
        for st in state.get("stages", []):
            stage_handler(st).rehydrate(st)
        self._state[group_id] = state

    def resume_units(self, group_id: int) -> list:
        ctx = self._ctx(group_id)
        if ctx is None:
            return []
        return stage_handler(ctx.stage).resume(ctx)

    # ── 流转决策（纯函数，返回 OrchestratorStep） ────────────────────────────────

    def begin(self, group_id: int, spec) -> OrchestratorStep:
        ordered_stages = spec["stages"] if isinstance(spec, dict) else spec
        self._state[group_id] = {"stages": ordered_stages, "current": 0}
        return OrchestratorStep(broadcast_state=True)

    def end(self, group_id: int) -> None:
        self._state.pop(group_id, None)

    def observe(self, group_id: int, bot_id: int, response: str) -> OrchestratorStep:
        ctx = self._ctx(group_id)
        if ctx is None:
            return OrchestratorStep()
        return stage_handler(ctx.stage).observe(ctx, bot_id, response)

    def advance(self, group_id: int, prev_output: str = "") -> OrchestratorStep:
        """手动推进（API /next 用）。"""
        return self._advance(group_id, prev_output)

    # ── 内部 ──────────────────────────────────────────────────────────────────

    def _advance(self, group_id: int, prev_output: str = "") -> OrchestratorStep:
        s = self._state.get(group_id)
        if not s:
            return OrchestratorStep()
        s.pop("awaiting_confirm", None)  # 推进即离开当前门
        s["current"] += 1
        if s["current"] >= len(s["stages"]):
            self.end(group_id)
            return OrchestratorStep(done=True)
        ctx = self._ctx(group_id)
        return stage_handler(ctx.stage).enter(ctx, prev_output)

    def _rewind(self, group_id: int, target_idx: int, prev_output: str = "") -> OrchestratorStep:
        """返工：把当前阶段回退到 target_idx（如 QA → Dev）并重新进入。给被回退到的
        bot 的 trigger 前面加一句【返工】说明，让它知道是按上游报告修复而非从头开发。"""
        s = self._state.get(group_id)
        if not s:
            return OrchestratorStep()
        s.pop("awaiting_confirm", None)
        s["current"] = target_idx
        ctx = self._ctx(group_id)
        if ctx is None:
            return OrchestratorStep()
        step = stage_handler(ctx.stage).enter(ctx, prev_output)
        for u in step.next_units:
            u.trigger_msg = (
                "【返工】QA 测试未通过。请根据上面 QA 的测试报告逐条修复问题，"
                "完成后重新自测，并按原要求在最后一行输出完成标记。\n\n" + u.trigger_msg
            )
        return step

    def _raise_gate(self, ctx: StageCtx, response: str, rework_to: int | None = None) -> OrchestratorStep:
        """bot 在带 gate 的阶段说出了完成/返工关键词：挂起本阶段、广播一张确认卡片，
        等用户 confirm() 才真正推进。response 暂存为下一步的 prev_output。

        rework_to 非空 → 这是一张「返工门」：确认后不是 _advance，而是 _rewind 回到
        rework_to 指向的阶段（门 id 加 -rework 后缀，与正常完成门区分）。"""
        s = self._state.get(ctx.group_id)
        if not s:
            return OrchestratorStep()
        stage = ctx.stage
        is_rework = rework_to is not None
        gate_id = f"{ctx.group_id}-{s['current']}" + ("-rework" if is_rework else "")
        s["awaiting_confirm"] = {"gate_id": gate_id, "prev_output": response, "rework_to": rework_to}
        if is_rework:
            label = stage.get("fail_gate_label") or f"「{stage.get('name', '')}」未通过，是否打回上一步修复？"
        else:
            label = stage.get("gate_label") or f"确认「{stage.get('name', '')}」这一步已完成"
        return OrchestratorStep(
            broadcast_state=True,
            confirm_gate={
                "gate_id": gate_id,
                "label": label,
                "bot_id": stage.get("id"),
                "stage_name": stage.get("name", ""),
            },
        )

    def confirm(self, group_id: int, gate_id: str | None = None) -> OrchestratorStep:
        s = self._state.get(group_id)
        if not s:
            return OrchestratorStep()
        pending = s.get("awaiting_confirm")
        if not pending:
            return OrchestratorStep()
        # 防重复 / 防过期：带了 gate_id 就必须对得上当前挂起的门。
        if gate_id is not None and gate_id != pending["gate_id"]:
            return OrchestratorStep()
        rework_to = pending.get("rework_to")
        if rework_to is not None:
            return self._rewind(group_id, rework_to, prev_output=pending.get("prev_output", ""))
        return self._advance(group_id, prev_output=pending.get("prev_output", ""))

    async def dispatch(self, group_id: int, message: dict, members: list, recent: list) -> OrchestratorStep:
        """DFT-071: Unified dispatch entry point for both workflow and free-form chat."""
        all_bots = [m for m in members if m["type"] == "bot"]
        content = (message.get("content") or "").strip()
        
        # 1. Check if there's an active workflow stage
        ctx = self._ctx(group_id)
        if ctx:
            # Workflow participant takes priority
            participant_bots = []
            wb = stage_handler(ctx.stage).current_bot(ctx.stage)
            if wb: participant_bots.append(wb)
            wp = stage_handler(ctx.stage).current_pool_bots(ctx.stage)
            if wp: participant_bots.extend([b for b in all_bots if b["id"] in wp])
            
            if participant_bots:
                # 首阶段由用户驱动、走这条路（而非 enter()），必须同样把工作流 system_suffix
                # 挂到 prompt_suffix 上，否则在岗 bot 收不到阶段指令（如“最后一行输出哨兵
                # [[BA_DONE]]”），确认门永远挂不起来、下一阶段永不交棒。
                suffix = self.system_suffix(group_id)
                return OrchestratorStep(next_units=[
                    WorkUnit(bot=b, executor_id=(b.get("executor_id") or "tool_loop_v1"),
                             trigger_msg=content, prompt_suffix=suffix, is_workflow=True)
                    for b in participant_bots
                ])

        # 2. Free-form chat routing. Priority: @mention → keyword match → active-bot
        #    lock. A single @mention or keyword match (re)sets the active bot, so a
        #    natural follow-up with no @ and no keyword still continues the
        #    conversation with the bot that last spoke (instead of going unanswered).
        from core.role_router import should_bot_respond
        explicit = [b for b in all_bots if f"@{b['name']}" in content]
        if "@all" in content.lower():
            explicit = all_bots

        target_bots = []
        if explicit:
            if len(explicit) == 1:
                await locks.set_active_bot(group_id, explicit[0]["id"])
            else:
                await locks.release_lock(group_id)
            target_bots = explicit
        else:
            matched = [b for b in all_bots if should_bot_respond(content, b["name"], b["role"] or "")]
            if matched:
                if len(matched) == 1:
                    await locks.set_active_bot(group_id, matched[0]["id"])
                target_bots = matched
            else:
                locked_bot_id = await locks.get_active_bot(group_id)
                locked = next((b for b in all_bots if b["id"] == locked_bot_id), None) if locked_bot_id else None
                target_bots = [locked] if locked else []

        if not target_bots:
            return OrchestratorStep()

        return OrchestratorStep(next_units=[
            WorkUnit(bot=b, trigger_msg=content)
            for b in target_bots
        ])

    def parse_spec(self, body: dict, all_bots: dict[int, dict]) -> dict:
        stages_cfg = body.get("stages", [])
        ordered = []
        for cfg in stages_cfg:
            if "pool" in cfg:
                pool_bots = [all_bots[b["bot_id"]] for b in cfg["pool"] if b["bot_id"] in all_bots]
                if pool_bots:
                    ordered.append({
                        "stage_type": "pool", "bots": pool_bots,
                        "done_keyword": cfg.get("done_keyword", "完毕"),
                        "in_progress": {}, "completed_tickets": [],
                        "ticket_queue": [], "idle_bots": [],
                    })
            else:
                bot = all_bots.get(cfg.get("bot_id"))
                if bot:
                    ordered.append({**bot, "stage_type": "single", "done_keyword": cfg.get("done_keyword", "完毕")})
        return {"stages": ordered}


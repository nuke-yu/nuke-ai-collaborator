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

    def system_suffix(self, group_id: int) -> str:
        ctx = self._ctx(group_id)
        if ctx is None:
            return ""
        idx, total = ctx.idx, ctx.total
        keyword = ctx.stage.get("done_keyword", "完毕")

        if idx + 1 < total:
            nxt = self._ctx(group_id, idx + 1)
            handler = stage_handler(nxt.stage)
            base = (f"\n\n[工作流 {idx+1}/{total}] 与用户多轮对话，充分澄清所有关键点后，"
                    f"当你认为本阶段工作真正完成时，在回复中说出「{keyword}」，"
                    f"系统会自动通知 {handler.display_name(nxt.stage)} 接棒。完成之前请不要说这句话。")
            return base + handler.incoming_requirement(nxt.stage, keyword)
        return (f"\n\n[工作流 {idx+1}/{total}] 这是最后一个阶段。"
                f"完成后说「{keyword}」作为收尾，给出完整的最终结论。")

    def snapshot(self, group_id: int) -> dict:
        s = self._state.get(group_id)
        if not s:
            return {"active": False}
        pending = s.get("awaiting_confirm")
        return {
            "active": True,
            "stages": [stage_handler(st).snapshot(st) for st in s["stages"]],
            "current": s["current"],
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

    def begin(self, group_id: int, ordered_stages: list) -> OrchestratorStep:
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

    def _raise_gate(self, ctx: StageCtx, response: str) -> OrchestratorStep:
        """bot 在带 gate 的阶段说出了完成关键词：挂起本阶段、广播一张确认卡片，
        等用户 confirm() 才真正 _advance。response 暂存为下一阶段的 prev_output。"""
        s = self._state.get(ctx.group_id)
        if not s:
            return OrchestratorStep()
        gate_id = f"{ctx.group_id}-{s['current']}"
        s["awaiting_confirm"] = {"gate_id": gate_id, "prev_output": response}
        stage = ctx.stage
        return OrchestratorStep(
            broadcast_state=True,
            confirm_gate={
                "gate_id": gate_id,
                "label": stage.get("gate_label") or f"确认「{stage.get('name', '')}」这一步已完成",
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
                return OrchestratorStep(next_units=[
                    WorkUnit(bot=b, trigger_msg=content)
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


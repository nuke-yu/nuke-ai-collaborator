"""
core/orchestration/plugins/round_robin.py — 第二个编排器（逃生舱示例）

存在的意义：证明 Orchestrator 契约真的可插拔。它和内置的 DeclarativeOrchestrator
是完全不同的拓扑——没有阶段、没有 done 关键词，就是固定一组 bot 按顺序轮流发言、
跑满 N 轮自动结束。它只依赖 base.Orchestrator 暴露的契约（begin/observe/snapshot/
current_bot/system_suffix/end/advance/serialize/restore/resume_units），不碰 stages。

spec = {"bots": [bot_dict, ...], "rounds": int}
"""
from core.orchestration.base import Orchestrator, OrchestratorStep, WorkUnit
import datetime


class RoundRobinOrchestrator(Orchestrator):
    orchestrator_id = "round_robin_v1"

    def __init__(self) -> None:
        # group_id -> {"bots": [...], "rounds": int, "idx": int, "round": int}
        self._state: dict[int, dict] = {}

    # ── 内部 ──────────────────────────────────────────────────────────────────

    def _unit(self, group_id: int, bot: dict, trigger_msg: str = "") -> WorkUnit:
        return WorkUnit(
            bot=bot, executor_id="tool_loop_v1",
            trigger_msg=trigger_msg or "请根据以上对话，发表你这一轮的看法。",
            prompt_suffix=self.system_suffix(group_id),
            is_workflow=True,
        )

    def _step_to_current(self, group_id: int) -> OrchestratorStep:
        s = self._state[group_id]
        bot = s["bots"][s["idx"]]
        return OrchestratorStep(next_units=[self._unit(group_id, bot)], broadcast_state=True)

    def _advance_cursor(self, group_id: int) -> OrchestratorStep:
        """游标前移一位，跑满 rounds 轮则结束。"""
        s = self._state.get(group_id)
        if not s:
            return OrchestratorStep()
        s["idx"] += 1
        if s["idx"] >= len(s["bots"]):
            s["idx"] = 0
            s["round"] += 1
        if s["round"] > s["rounds"]:
            self.end(group_id)
            return OrchestratorStep(done=True)
        return self._step_to_current(group_id)

    # ── 契约：决策 ──────────────────────────────────────────────────────────────

    def begin(self, group_id: int, spec) -> OrchestratorStep:
        bots = list(spec.get("bots", []))
        rounds = int(spec.get("rounds", 1))
        if not bots or rounds < 1:
            return OrchestratorStep()
        self._state[group_id] = {
            "bots": bots,
            "rounds": rounds,
            "idx": 0,
            "round": 1,
            "started": False,
            "start_time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        }
        return OrchestratorStep(broadcast_state=True)

    async def dispatch(self, group_id: int, message: dict, members: list, recent: list) -> OrchestratorStep:
        """User's first message triggers the first round; subsequent messages are ignored."""
        s = self._state.get(group_id)
        if not s or s.get("started"):
            return OrchestratorStep()
        content = (message.get("content") or "").strip()
        if not content:
            return OrchestratorStep()
        s["started"] = True
        bot = s["bots"][s["idx"]]
        return OrchestratorStep(
            next_units=[self._unit(group_id, bot, trigger_msg=content)],
            broadcast_state=True,
        )

    def observe(self, group_id: int, bot_id: int, response: str) -> OrchestratorStep:
        s = self._state.get(group_id)
        if not s or s["bots"][s["idx"]].get("id") != bot_id:
            return OrchestratorStep()
        return self._advance_cursor(group_id)

    def advance(self, group_id: int, prev_output: str = "") -> OrchestratorStep:
        return self._advance_cursor(group_id)

    def end(self, group_id: int) -> None:
        self._state.pop(group_id, None)

    # ── 契约：查询 ──────────────────────────────────────────────────────────────

    def current_bot(self, group_id: int) -> dict | None:
        s = self._state.get(group_id)
        return s["bots"][s["idx"]] if s else None

    def system_suffix(self, group_id: int) -> str:
        s = self._state.get(group_id)
        if not s:
            return ""
        return (f"\n\n[轮转 第{s['round']}/{s['rounds']}轮] 简短发表你这一轮的看法，"
                f"说完即可，系统会自动轮到下一位。")

    def snapshot(self, group_id: int) -> dict:
        s = self._state.get(group_id)
        if not s:
            return {"active": False}
        return {
            "active": True, "type": "round_robin",
            "round": s["round"], "rounds": s["rounds"], "current": s["idx"],
            "bots": [{"id": b.get("id"), "name": b.get("name", "")} for b in s["bots"]],
        }

    # ── 契约：持久化 / 恢复 ──────────────────────────────────────────────────────

    def serialize(self, group_id: int) -> dict | None:
        return self._state.get(group_id)

    def restore(self, group_id: int, state: dict) -> None:
        self._state[group_id] = state

    def resume_units(self, group_id: int) -> list:
        s = self._state.get(group_id)
        if not s or not s.get("started"):
            return []
        return [self._unit(group_id, s["bots"][s["idx"]])]

    def start_time(self, group_id: int) -> str | None:
        s = self._state.get(group_id)
        return s.get("start_time") if s else None

    def parse_spec(self, body: dict, all_bots: dict[int, dict]) -> dict:
        spec = dict(body.get("spec", {}))
        if "bots" in spec:
            spec["bots"] = [all_bots[bid] for bid in spec["bots"] if bid in all_bots]
        return spec

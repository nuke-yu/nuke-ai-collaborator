"""
core/orchestration/plugins/discussion.py — Multi-Bot Discussion Scenario

Allows a group of bots to discuss a topic in sequence (round-robin) for a configurable number of rounds.
At the end of the discussion rounds, a designated bot automatically writes a final summary and
compiles a comparative pros/cons evaluation of all viewpoints.
"""
from core.orchestration.base import Orchestrator, OrchestratorStep, WorkUnit
import datetime


class DiscussionOrchestrator(Orchestrator):
    orchestrator_id = "discussion_v1"

    def __init__(self) -> None:
        # group_id -> {"bots": [...], "rounds": int, "idx": int, "round": int, "phase": str, "summarizer": dict}
        self._state: dict[int, dict] = {}

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _unit(self, group_id: int, bot: dict) -> WorkUnit:
        return WorkUnit(
            bot=bot,
            executor_id="tool_loop_v1",
            prompt_suffix=self.system_suffix(group_id),
        )

    def _step_to_current(self, group_id: int) -> OrchestratorStep:
        s = self._state[group_id]
        if s["phase"] == "summary":
            bot = s["summarizer"]
        else:
            bot = s["bots"][s["idx"]]
        return OrchestratorStep(next_units=[self._unit(group_id, bot)], broadcast_state=True)

    def _advance_cursor(self, group_id: int) -> OrchestratorStep:
        s = self._state.get(group_id)
        if not s:
            return OrchestratorStep()

        if s["phase"] == "summary":
            # Summarizer bot finished speaking, end the workflow
            self.end(group_id)
            return OrchestratorStep(done=True)

        # Discussion phase: advance turn cursor to the next bot
        s["idx"] += 1
        if s["idx"] >= len(s["bots"]):
            s["idx"] = 0
            s["round"] += 1

        if s["round"] > s["rounds"]:
            # Transition to the summary phase
            s["phase"] = "summary"

        return self._step_to_current(group_id)

    # ── Orchestrator Interface: Decision ──────────────────────────────────────

    def begin(self, group_id: int, spec) -> OrchestratorStep:
        bots = list(spec.get("bots", []))
        rounds = int(spec.get("rounds", 1))
        summarizer_id = spec.get("summarizer_id")

        # Resolve summarizer bot dict
        summarizer = None
        if summarizer_id:
            for b in bots:
                if b.get("id") == summarizer_id:
                    summarizer = b
                    break
        if not summarizer and bots:
            summarizer = bots[0]

        if not bots or rounds < 1:
            return OrchestratorStep()

        self._state[group_id] = {
            "bots": bots,
            "rounds": rounds,
            "idx": 0,
            "round": 1,
            "phase": "discussion",
            "summarizer": summarizer,
            "start_time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "viewpoints_summary": {},
        }
        return self._step_to_current(group_id)

    def observe(self, group_id: int, bot_id: int, response: str) -> OrchestratorStep:
        s = self._state.get(group_id)
        if not s:
            return OrchestratorStep()

        current_active_bot = self.current_bot(group_id)
        if not current_active_bot or current_active_bot.get("id") != bot_id:
            return OrchestratorStep()

        return self._advance_cursor(group_id)

    def advance(self, group_id: int, prev_output: str = "") -> OrchestratorStep:
        return self._advance_cursor(group_id)

    def end(self, group_id: int) -> None:
        self._state.pop(group_id, None)

    # ── Orchestrator Interface: Queries ───────────────────────────────────────

    def current_bot(self, group_id: int) -> dict | None:
        s = self._state.get(group_id)
        if not s:
            return None
        if s["phase"] == "summary":
            return s["summarizer"]
        return s["bots"][s["idx"]]

    def system_suffix(self, group_id: int) -> str:
        s = self._state.get(group_id)
        if not s:
            return ""
        if s["phase"] == "summary":
            return (
                f"\n\n[Workflow: Multi-Bot Discussion Summary Phase]\n"
                f"The discussion rounds have concluded. As the designated summarizer bot, your task is to:\n"
                f"1. Summarize the key arguments, ideas, and conclusions from the preceding discussion.\n"
                f"2. Provide a comparative evaluation (highlighting pros and cons) of the different viewpoints/proposals.\n"
                f"3. Keep your output well-structured, objective, and professional."
            )
        else:
            rounds = s["rounds"]
            current_round = s["round"]
            
            # Calculate the round where convergence starts (last 20% of rounds, at least Round 2 if rounds > 1)
            convergence_start = max(2, rounds - max(1, int(rounds * 0.2)) + 1)
            
            if current_round == 1:
                return (
                    f"\n\n[Workflow: Multi-Bot Discussion Round 1/{rounds} (立论阶段/Thesis)]\n"
                    f"这是第一轮（立论阶段）。请根据你的人物设定和专业背景，简明扼要、逻辑严密地阐述你对讨论主题的核心观点与基本立场。\n"
                    f"请直接发表专业见解，保持发言精炼，不要有任何客套。发言结束后系统会自动切换到下一个 Bot。\n"
                    f"(This is Round 1 - Thesis. Please logically and concisely state your initial stance and core arguments based on your persona and background.)"
                )
            elif current_round >= convergence_start:
                return (
                    f"\n\n[Workflow: Multi-Bot Discussion Round {current_round}/{rounds} (收敛共识阶段/Synthesis)]\n"
                    f"这是第 {current_round} 轮（收敛共识与最终发言阶段）。讨论已进入尾声，请逐步统一观点。\n"
                    f"请理性审视前几轮其他 Bot 的建设性意见与反驳，吸纳合理部分，寻求共识。请修正或融合出更优的最终方案，避免盲目固执，并在此完成你的最终表态。\n"
                    f"请直接阐述你的融合见解与共识方向，保持精炼。发言结束后系统会自动切换到下一个 Bot，最后一轮结束后将由总结者输出报告。\n"
                    f"(This is Round {current_round} - Synthesis/Final Statements. The debate is ending. Please seek consensus, acknowledge valid criticisms, and integrate ideas into a unified, optimized final proposal.)"
                )
            else:
                return (
                    f"\n\n[Workflow: Multi-Bot Discussion Round {current_round}/{rounds} (交锋对抗阶段/Antithesis)]\n"
                    f"这是第 {current_round} 轮（交锋对抗阶段）。请批判性、挑剔性地审视前面其他 Bot 发表的言论，指出其观点的不足、盲点或局限性。\n"
                    f"在此基础上，强化你自己的论据支撑，证明你的观点比其他人更加准确、犀利、睿智和逻辑严密。请直接交锋，避免客套。\n"
                    f"请直接发表你的深刻辩驳，保持精炼。发言结束后系统会自动切换到下一个 Bot。\n"
                    f"(This is Round {current_round} - Antithesis. Please critically review the arguments of other bots, point out their blind spots or knowledge gaps, and reinforce your own position to prove your views are sharper and more accurate.)"
                )

    def snapshot(self, group_id: int) -> dict:
        s = self._state.get(group_id)
        if not s:
            return {"active": False}
        stages = [
            {
                "stage_type": "single",
                "name": f"Discussion (Round {s['round']}/{s['rounds']})",
                "avatar_color": "#818cf8",
            },
            {
                "stage_type": "single",
                "name": f"Summary & Pros/Cons ({s['summarizer'].get('name', 'Summarizer')})",
                "avatar_color": "#10b981",
            },
        ]
        current = 0 if s["phase"] == "discussion" else 1
        return {
            "active": True,
            "type": "discussion",
            "stages": stages,
            "current": current,
            "round": s["round"],
            "rounds": s["rounds"],
            "idx": s["idx"],
            "phase": s["phase"],
            "bots": [{"id": b.get("id"), "name": b.get("name", "")} for b in s["bots"]],
            "summarizer": {"id": s["summarizer"].get("id"), "name": s["summarizer"].get("name", "")},
        }

    def snapshot_state(self, state: dict | None) -> dict:
        if not state:
            return {"active": False}
        stages = [
            {
                "stage_type": "single",
                "name": f"Discussion (Round {state['round']}/{state['rounds']})",
                "avatar_color": "#818cf8",
            },
            {
                "stage_type": "single",
                "name": f"Summary & Pros/Cons ({state['summarizer'].get('name', 'Summarizer')})",
                "avatar_color": "#10b981",
            },
        ]
        current = 0 if state["phase"] == "discussion" else 1
        return {
            "active": True,
            "type": "discussion",
            "stages": stages,
            "current": current,
            "round": state["round"],
            "rounds": state["rounds"],
            "idx": state["idx"],
            "phase": state["phase"],
            "bots": [{"id": b.get("id"), "name": b.get("name", "")} for b in state["bots"]],
            "summarizer": {"id": state["summarizer"].get("id"), "name": state["summarizer"].get("name", "")},
        }

    # ── Orchestrator Interface: Serialization / Recovery ─────────────────────

    def serialize(self, group_id: int) -> dict | None:
        return self._state.get(group_id)

    def restore(self, group_id: int, state: dict) -> None:
        self._state[group_id] = state

    def resume_units(self, group_id: int) -> list:
        s = self._state.get(group_id)
        if not s:
            return []
        bot = s["summarizer"] if s["phase"] == "summary" else s["bots"][s["idx"]]
        return [self._unit(group_id, bot)]

    def start_time(self, group_id: int) -> str | None:
        s = self._state.get(group_id)
        return s.get("start_time") if s else None

    def parse_spec(self, body: dict, all_bots: dict[int, dict]) -> dict:
        spec = dict(body.get("spec", {}))
        if "bots" in spec:
            spec["bots"] = [all_bots[bid] for bid in spec["bots"] if bid in all_bots]
        return spec

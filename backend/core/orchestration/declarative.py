"""
core/orchestration/declarative.py — 数据驱动编排器（内置默认）

把"编排即数据"落地：阶段定义就是 start() 传进来的 ordered_stages 列表，
本类是一个通用解释器，按 stage_type（single / pool）驱动流转。
所有方法都是纯决策：只读写内部 _state，返回 OrchestratorStep，绝不发事件 / 调 AI。
"""
import random
import re

from core.orchestration.base import Orchestrator, OrchestratorStep, WorkUnit, SystemMessage


def parse_tickets(message: str) -> list[str]:
    match = re.search(r'TICKETS:\s*\n((?:(?:\d+\.|[-*+])\s+.+\n?)+)', message, re.IGNORECASE)
    if match:
        items = re.findall(r'(?:\d+\.|[-*+])\s+(.+)', match.group(1))
        if items:
            return [t.strip() for t in items[:20]]
    items = re.findall(r'^\s*(?:\d+\.|[-*+])\s+(.+)$', message, re.MULTILINE)
    if len(items) >= 2:
        return [t.strip() for t in items[:20]]
    return ["本次迭代任务"]


class DeclarativeOrchestrator(Orchestrator):
    orchestrator_id = "workflow_v1"

    def __init__(self) -> None:
        # group_id -> { stages: [...], current: int }
        self._state: dict[int, dict] = {}

    # ── 状态查询（runner / core.orchestrator 用） ──────────────────────────────

    def get(self, group_id: int) -> dict | None:
        return self._state.get(group_id)

    def current_bot(self, group_id: int) -> dict | None:
        s = self._state.get(group_id)
        if not s or s["current"] >= len(s["stages"]):
            return None
        stage = s["stages"][s["current"]]
        return None if stage.get("stage_type") == "pool" else stage

    def current_pool_bots(self, group_id: int) -> list[int] | None:
        s = self._state.get(group_id)
        if not s or s["current"] >= len(s["stages"]):
            return None
        stage = s["stages"][s["current"]]
        if stage.get("stage_type") != "pool":
            return None
        in_progress = stage.get("in_progress", {})
        return list(in_progress.keys()) if in_progress else [b["id"] for b in stage["bots"]]

    def system_suffix(self, group_id: int) -> str:
        s = self._state.get(group_id)
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

    def snapshot(self, group_id: int) -> dict:
        s = self._state.get(group_id)
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

    # ── 流转决策（纯函数，返回 OrchestratorStep） ────────────────────────────────

    def begin(self, group_id: int, ordered_stages: list) -> OrchestratorStep:
        self._state[group_id] = {"stages": ordered_stages, "current": 0}
        return OrchestratorStep(broadcast_state=True)

    def end(self, group_id: int) -> None:
        self._state.pop(group_id, None)

    def observe(self, group_id: int, bot_id: int, response: str) -> OrchestratorStep:
        s = self._state.get(group_id)
        if not s:
            return OrchestratorStep()
        stage = s["stages"][s["current"]]

        if stage.get("stage_type") == "pool":
            keyword = stage.get("done_keyword", "")
            if not (keyword and keyword in response):
                return OrchestratorStep()
            in_progress = stage.get("in_progress", {})
            if bot_id is None or bot_id not in in_progress:
                return OrchestratorStep()

            step = OrchestratorStep()
            done_ticket = in_progress.pop(bot_id)
            stage.setdefault("completed_tickets", []).append(done_ticket)
            queue = stage.get("ticket_queue", [])
            bot_dict = next((b for b in stage["bots"] if b["id"] == bot_id), None)

            if queue:
                next_ticket = queue.pop(0)
                in_progress[bot_id] = next_ticket
                step.broadcast_state = True
                if bot_dict:
                    step.announcements.append(SystemMessage(
                        f"✅ **{bot_dict['name']}** 完成「{done_ticket}」，认领下一个：「{next_ticket}」",
                        bot_dict["id"]))
                    step.next_units.append(self._pool_unit(group_id, bot_dict, next_ticket, stage))
                return step

            # 没有更多任务了 —— 该 bot 进入空闲
            stage.setdefault("idle_bots", []).append(bot_id)
            if bot_dict:
                step.announcements.append(SystemMessage(
                    f"✅ **{bot_dict['name']}** 完成「{done_ticket}」，等待其他任务", bot_dict["id"]))

            if not in_progress:
                # 全部完成 → 进入下一阶段（done 时不再重复广播 active 快照）
                adv = self._advance(group_id, prev_output=response)
                step.next_units.extend(adv.next_units)
                step.announcements.extend(adv.announcements)
                step.done = adv.done
                step.broadcast_state = adv.broadcast_state
                return step
            step.broadcast_state = True
            return step

        # single
        keyword = stage.get("done_keyword", "")
        if keyword and keyword in response:
            return self._advance(group_id, prev_output=response)
        return OrchestratorStep()

    def advance(self, group_id: int, prev_output: str = "") -> OrchestratorStep:
        """手动推进（API /next 用）。"""
        return self._advance(group_id, prev_output)

    # ── 内部 ──────────────────────────────────────────────────────────────────

    def _advance(self, group_id: int, prev_output: str = "") -> OrchestratorStep:
        s = self._state.get(group_id)
        if not s:
            return OrchestratorStep()
        s["current"] += 1
        if s["current"] >= len(s["stages"]):
            self.end(group_id)
            return OrchestratorStep(done=True)
        return self._enter_stage(group_id, prev_output)

    def _enter_stage(self, group_id: int, prev_output: str) -> OrchestratorStep:
        s = self._state[group_id]
        stage = s["stages"][s["current"]]
        step = OrchestratorStep(broadcast_state=True)
        if stage.get("stage_type") == "pool":
            return self._enter_pool(group_id, stage, prev_output, step)
        step.next_units.append(WorkUnit(
            bot=stage,
            executor_id=stage.get("executor_id", "simple_v1"),
            trigger_msg=f"请开始你（{stage['name']} · {stage.get('role', '')}）的工作。",
            prompt_suffix=self.system_suffix(group_id),
        ))
        return step

    def _enter_pool(self, group_id: int, pool_stage: dict, prev_output: str,
                    step: OrchestratorStep) -> OrchestratorStep:
        tickets = parse_tickets(prev_output)
        bots = list(pool_stage["bots"])
        random.shuffle(bots)

        initial = min(len(bots), len(tickets))
        pool_stage["ticket_queue"] = list(tickets[initial:])
        pool_stage["in_progress"] = {bots[i]["id"]: tickets[i] for i in range(initial)}
        pool_stage["completed_tickets"] = []
        pool_stage["idle_bots"] = []

        lines = [f"🎯 共 {len(tickets)} 个开发任务，{len(bots)} 位开发者开始认领！"]
        for i in range(initial):
            lines.append(f"✅ **{bots[i]['name']}** 认领了「{tickets[i]}」")
        for bot in bots[initial:]:
            lines.append(f"⏳ **{bot['name']}** 等待认领")
        step.announcements.append(SystemMessage("\n".join(lines), bots[0]["id"]))

        for i in range(initial):
            step.next_units.append(self._pool_unit(group_id, bots[i], tickets[i], pool_stage))
        return step

    def _pool_unit(self, group_id: int, bot: dict, ticket: str, pool_stage: dict) -> WorkUnit:
        s = self._state[group_id]
        idx = s["current"]
        total = len(s["stages"])
        keyword = pool_stage.get("done_keyword", "完毕")
        suffix = (
            f"\n\n[工作流 {idx+1}/{total}] 你当前认领的任务：「{ticket}」\n"
            f"请完成这个任务，描述你的实现方案、关键代码思路，并给出 commit message 和 PR 描述。\n"
            f"完成后在回复末尾说「{keyword}」，系统会自动为你分配下一个任务（如果有的话）。"
        )
        return WorkUnit(
            bot=bot,
            executor_id=bot.get("executor_id", "simple_v1"),
            trigger_msg=f"开始开发任务：{ticket}",
            prompt_suffix=suffix,
            tag={"ticket": ticket},
        )

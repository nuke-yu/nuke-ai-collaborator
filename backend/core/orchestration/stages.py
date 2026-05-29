"""
core/orchestration/stages.py — 阶段类型（数据驱动 + 可插拔）

工作流的 spec 就是数据：ordered_stages 里每个 stage 带一个 stage_type 字符串。
本模块把"某个 stage_type 怎么进入 / 怎么流转 / 怎么序列化"做成按名注册的 handler，
DeclarativeOrchestrator 退化成纯派发器（核心里再没有 `if stage_type == "pool"`）。

加一个新阶段类型 = 写一个 StageType 子类并 register_stage_type()，无需改编排器核心。
"""
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.orchestration.base import OrchestratorStep, WorkUnit, SystemMessage


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


@dataclass
class StageCtx:
    """一次阶段操作的上下文。orch 用鸭子类型（DeclarativeOrchestrator），不在此 import 以免环。"""
    orch: object
    group_id: int
    stage: dict
    idx: int
    total: int


class StageType(ABC):
    """阶段类型契约。spec 里的 stage_type 字符串 → 这里的一个实例。"""
    name: str = ""

    # ── 参与者模型（normal message 路由用） ──
    def current_bot(self, stage: dict) -> dict | None:
        return None

    def current_pool_bots(self, stage: dict) -> list[int] | None:
        return None

    # ── 交接提示（数据驱动 system_suffix） ──
    def display_name(self, stage: dict) -> str:
        """上一阶段被告知"通知 X 接棒"时显示的名字。"""
        return stage.get("name", "下一阶段")

    def incoming_requirement(self, stage: dict, prev_keyword: str) -> str:
        """上一阶段在收尾前必须产出的东西（如 pool 需要一份 TICKETS 列表）。"""
        return ""

    # ── 流转 ──
    @abstractmethod
    def enter(self, ctx: StageCtx, prev_output: str) -> OrchestratorStep:
        """流程推进到本阶段时触发的首批工作。"""

    @abstractmethod
    def observe(self, ctx: StageCtx, bot_id: int, response: str) -> OrchestratorStep:
        """本阶段某个 bot 跑完一轮后的决策。"""

    @abstractmethod
    def snapshot(self, stage: dict) -> dict:
        """WorkflowUpdate 用的单阶段序列化。"""


class SingleStage(StageType):
    name = "single"

    def current_bot(self, stage: dict) -> dict | None:
        return stage

    def display_name(self, stage: dict) -> str:
        return stage["name"]

    def enter(self, ctx: StageCtx, prev_output: str) -> OrchestratorStep:
        stage = ctx.stage
        return OrchestratorStep(
            broadcast_state=True,
            next_units=[WorkUnit(
                bot=stage,
                executor_id=stage.get("executor_id", "simple_v1"),
                trigger_msg=f"请开始你（{stage['name']} · {stage.get('role', '')}）的工作。",
                prompt_suffix=ctx.orch.system_suffix(ctx.group_id),
            )],
        )

    def observe(self, ctx: StageCtx, bot_id: int, response: str) -> OrchestratorStep:
        keyword = ctx.stage.get("done_keyword", "")
        if keyword and keyword in response:
            return ctx.orch._advance(ctx.group_id, prev_output=response)
        return OrchestratorStep()

    def snapshot(self, stage: dict) -> dict:
        return {
            "stage_type": "single",
            "id": stage["id"], "name": stage["name"],
            "avatar_color": stage["avatar_color"], "role": stage.get("role") or "",
            "done_keyword": stage.get("done_keyword", ""),
        }


class PoolStage(StageType):
    name = "pool"

    def current_pool_bots(self, stage: dict) -> list[int] | None:
        in_progress = stage.get("in_progress", {})
        return list(in_progress.keys()) if in_progress else [b["id"] for b in stage["bots"]]

    def display_name(self, stage: dict) -> str:
        return "开发团队"

    def incoming_requirement(self, stage: dict, prev_keyword: str) -> str:
        dev_count = len(stage["bots"])
        return (
            f"\n\n在说「{prev_keyword}」之前，请用以下格式列出所有需开发的任务：\n\n"
            f"TICKETS:\n1. 任务名称（一句话描述）\n2. 任务名称\n3. 任务名称\n...\n\n"
            f"根据实际工作量拆分，任务数量不限（有 {dev_count} 位开发者会依次认领）。"
        )

    def enter(self, ctx: StageCtx, prev_output: str) -> OrchestratorStep:
        stage = ctx.stage
        tickets = parse_tickets(prev_output)
        bots = list(stage["bots"])
        random.shuffle(bots)

        initial = min(len(bots), len(tickets))
        stage["ticket_queue"] = list(tickets[initial:])
        stage["in_progress"] = {bots[i]["id"]: tickets[i] for i in range(initial)}
        stage["completed_tickets"] = []
        stage["idle_bots"] = []

        lines = [f"🎯 共 {len(tickets)} 个开发任务，{len(bots)} 位开发者开始认领！"]
        for i in range(initial):
            lines.append(f"✅ **{bots[i]['name']}** 认领了「{tickets[i]}」")
        for bot in bots[initial:]:
            lines.append(f"⏳ **{bot['name']}** 等待认领")

        step = OrchestratorStep(broadcast_state=True)
        step.announcements.append(SystemMessage("\n".join(lines), bots[0]["id"]))
        for i in range(initial):
            step.next_units.append(self._unit(ctx, bots[i], tickets[i]))
        return step

    def observe(self, ctx: StageCtx, bot_id: int, response: str) -> OrchestratorStep:
        stage = ctx.stage
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
                step.next_units.append(self._unit(ctx, bot_dict, next_ticket))
            return step

        stage.setdefault("idle_bots", []).append(bot_id)
        if bot_dict:
            step.announcements.append(SystemMessage(
                f"✅ **{bot_dict['name']}** 完成「{done_ticket}」，等待其他任务", bot_dict["id"]))

        if not in_progress:
            adv = ctx.orch._advance(ctx.group_id, prev_output=response)
            step.next_units.extend(adv.next_units)
            step.announcements.extend(adv.announcements)
            step.done = adv.done
            step.broadcast_state = adv.broadcast_state
            return step
        step.broadcast_state = True
        return step

    def snapshot(self, stage: dict) -> dict:
        in_prog = stage.get("in_progress", {})
        done_tickets = stage.get("completed_tickets", [])
        queue = stage.get("ticket_queue", [])
        return {
            "stage_type": "pool",
            "bots": [{"id": b["id"], "name": b["name"], "avatar_color": b["avatar_color"]}
                     for b in stage["bots"]],
            "in_progress": {str(k): v for k, v in in_prog.items()},
            "completed_count": len(done_tickets),
            "total_tickets": len(done_tickets) + len(in_prog) + len(queue),
            "done_keyword": stage.get("done_keyword", ""),
        }

    def _unit(self, ctx: StageCtx, bot: dict, ticket: str) -> WorkUnit:
        keyword = ctx.stage.get("done_keyword", "完毕")
        suffix = (
            f"\n\n[工作流 {ctx.idx+1}/{ctx.total}] 你当前认领的任务：「{ticket}」\n"
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


# ── 阶段类型注册表（可插拔） ──────────────────────────────────────────────────

_STAGE_TYPES: dict[str, StageType] = {}


def register_stage_type(handler: StageType) -> None:
    _STAGE_TYPES[handler.name] = handler


def stage_handler(stage: dict) -> StageType:
    """按 spec 里的 stage_type 取 handler，未知类型回退 single。"""
    return _STAGE_TYPES.get(stage.get("stage_type", "single"), _STAGE_TYPES["single"])


def all_stage_types() -> list[str]:
    return list(_STAGE_TYPES.keys())


register_stage_type(SingleStage())
register_stage_type(PoolStage())

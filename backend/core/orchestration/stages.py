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

    # ── 崩溃恢复 ──
    def rehydrate(self, stage: dict) -> None:
        """JSON round-trip 后修复 stage 内的类型（如 int bot_id key 被转成 str）。
        默认无需修复；用 int key 的阶段类型（pool/verification）需覆写。"""

    def resume(self, ctx: StageCtx) -> list:
        """崩溃恢复后需要重新派发的在飞工作单元（list[WorkUnit]）。
        默认无（如 single 由用户对话驱动，重启后用户继续发言即可）。"""
        return []


class SingleStage(StageType):
    name = "single"

    def current_bot(self, stage: dict) -> dict | None:
        return stage

    def display_name(self, stage: dict) -> str:
        return stage["name"]

    def enter(self, ctx: StageCtx, prev_output: str) -> OrchestratorStep:
        stage = ctx.stage
        # 带 instruction 的阶段（如 RD 流水线的建Jira/Dev/QA 棒）用本阶段任务指令开场，
        # 交棒时下一个 bot 一进场就知道该干什么；否则回退到通用开场白。
        trigger = stage.get("instruction") or \
            f"请开始你（{stage['name']} · {stage.get('role', '')}）的工作。"
        return OrchestratorStep(
            broadcast_state=True,
            next_units=[WorkUnit(
                bot=stage,
                executor_id=stage.get("executor_id", "tool_loop_v1"),
                trigger_msg=trigger,
                prompt_suffix=ctx.orch.system_suffix(ctx.group_id),
            )],
        )

    def observe(self, ctx: StageCtx, bot_id: int, response: str) -> OrchestratorStep:
        keyword = ctx.stage.get("done_keyword", "")
        if keyword and keyword in response:
            # 带 gate 的阶段：bot 说完成不直接推进，而是挂起等人点「确认」。
            if ctx.stage.get("gate"):
                return ctx.orch._raise_gate(ctx, response)
            return ctx.orch._advance(ctx.group_id, prev_output=response)
        return OrchestratorStep()

    def snapshot(self, stage: dict) -> dict:
        return {
            "stage_type": "single",
            "id": stage["id"], "name": stage["name"],
            "avatar_color": stage["avatar_color"], "role": stage.get("role") or "",
            "done_keyword": stage.get("done_keyword", ""),
            "gate": bool(stage.get("gate")),
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
        pairs, queue = self._assign(bots, tickets)

        stage["ticket_queue"] = queue
        stage["in_progress"] = {b["id"]: t for b, t in pairs}
        stage["completed_tickets"] = []
        stage["idle_bots"] = []

        assigned = {b["id"] for b, _ in pairs}
        lines = [f"🎯 共 {len(tickets)} 个开发任务，{len(bots)} 位开发者开始认领！"]
        for b, t in pairs:
            lines.append(f"✅ **{b['name']}** 认领了「{t}」")
        for b in bots:
            if b["id"] not in assigned:
                lines.append(f"⏳ **{b['name']}** 等待认领")

        step = OrchestratorStep(broadcast_state=True)
        step.announcements.append(SystemMessage("\n".join(lines), bots[0]["id"]))
        for b, t in pairs:
            step.next_units.append(self._unit(ctx, b, t))
        return step

    def _assign(self, bots: list[dict], tickets: list[str]) -> tuple[list, list]:
        """把任务分给 bot：擅长该任务的优先认领，无人擅长的任务回到默认（随机）认领。

        返回 (pairs, queue)：pairs=[(bot, ticket)] 立即开工；queue=排队待领的任务。
        pairs 数量 = min(len(bots), len(tickets))。
        """
        available = list(bots)
        pairs: list[tuple[dict, str]] = []
        unmatched: list[str] = []

        # 第一轮：擅长优先 —— 按任务顺序，每个任务挑当前可用 bot 里得分最高者
        for ticket in tickets:
            best, best_score = None, 0
            for bot in available:
                score = self._expertise_score(bot, ticket)
                if score > best_score:
                    best, best_score = bot, score
            if best is not None:
                available.remove(best)
                pairs.append((best, ticket))
            else:
                unmatched.append(ticket)

        # 第二轮：无人擅长的任务回到默认 —— 随机认领
        random.shuffle(available)
        queue: list[str] = []
        for ticket in unmatched:
            if available:
                pairs.append((available.pop(0), ticket))
            else:
                queue.append(ticket)
        return pairs, queue

    def _expertise_score(self, bot: dict, ticket: str) -> int:
        """bot 对某任务的擅长度：擅长关键词在任务文本里命中的个数。0 表示不擅长。"""
        kws = bot.get("expertise")
        if isinstance(kws, str):
            kws = re.split(r'[,，、/\s]+', kws)
        if not kws:
            kws = re.split(r'[,，、/\s]+', bot.get("role") or "")
        t = ticket.lower()
        return sum(1 for k in kws if k and k.strip() and k.strip().lower() in t)

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

    def rehydrate(self, stage: dict) -> None:
        ip = stage.get("in_progress")
        if isinstance(ip, dict):
            stage["in_progress"] = {int(k): v for k, v in ip.items()}

    def resume(self, ctx: StageCtx) -> list:
        units = []
        for bot_id, ticket in ctx.stage.get("in_progress", {}).items():
            bot = next((b for b in ctx.stage["bots"] if b["id"] == bot_id), None)
            if bot:
                units.append(self._unit(ctx, bot, ticket))
        return units

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
            executor_id=bot.get("executor_id", "tool_loop_v1"),
            trigger_msg=f"开始开发任务：{ticket}",
            prompt_suffix=suffix,
            tag={"ticket": ticket},
        )


class DiscussionStage(StageType):
    """讨论阶段：成员轮流发言（round-robin），循环讨论固定轮数，最后一轮给出结论再推进。

    与 single/pool 都不同的流转模式：严格串行（同一时刻只有一位发言），靠"轮次计数"终止，
    既不是单人独占，也不是按任务认领。spec 里用 `rounds` 配置总轮数（默认 6）。
    （插拔验证用：新增本类完全不需要改 declarative.py 编排器核心。）
    """
    name = "discussion"

    def _rounds(self, stage: dict) -> int:
        return int(stage.get("rounds", 6))

    def current_pool_bots(self, stage: dict) -> list[int] | None:
        speaker = stage.get("speaker")
        return [speaker] if speaker is not None else [b["id"] for b in stage["bots"]]

    def display_name(self, stage: dict) -> str:
        return stage.get("name", "讨论组")

    def enter(self, ctx: StageCtx, prev_output: str) -> OrchestratorStep:
        stage = ctx.stage
        bots = stage["bots"]
        stage["round"] = 1
        stage["speaker"] = bots[0]["id"]

        step = OrchestratorStep(broadcast_state=True)
        names = "、".join(b["name"] for b in bots)
        step.announcements.append(SystemMessage(
            f"💬 讨论开始（共 {self._rounds(stage)} 轮）：{names}", bots[0]["id"]))
        step.next_units.append(self._unit(ctx, bots[0], 1))
        return step

    def observe(self, ctx: StageCtx, bot_id: int, response: str) -> OrchestratorStep:
        stage = ctx.stage
        if bot_id is None or bot_id != stage.get("speaker"):
            return OrchestratorStep()

        rnd = stage.get("round", 1)
        if rnd >= self._rounds(stage):
            # 最后一轮发言完毕 → 推进到下一阶段
            return ctx.orch._advance(ctx.group_id, prev_output=response)

        bots = stage["bots"]
        nxt = bots[rnd % len(bots)]  # 下一位发言者（round-robin）
        stage["round"] = rnd + 1
        stage["speaker"] = nxt["id"]

        step = OrchestratorStep(broadcast_state=True)
        step.next_units.append(self._unit(ctx, nxt, rnd + 1))
        return step

    def resume(self, ctx: StageCtx) -> list:
        stage = ctx.stage
        speaker = stage.get("speaker")
        if speaker is None:
            return []
        bot = next((b for b in stage["bots"] if b["id"] == speaker), None)
        return [self._unit(ctx, bot, stage.get("round", 1))] if bot else []

    def snapshot(self, stage: dict) -> dict:
        bots = stage["bots"]
        return {
            "stage_type": "discussion",
            "bots": [{"id": b["id"], "name": b["name"], "avatar_color": b["avatar_color"]}
                     for b in bots],
            "round": stage.get("round", 0),
            "rounds": self._rounds(stage),
            "speaker": stage.get("speaker"),
        }

    def _unit(self, ctx: StageCtx, bot: dict, rnd: int) -> WorkUnit:
        total = self._rounds(ctx.stage)
        if rnd >= total:
            task = "这是最后一轮，请综合前面所有人的讨论，给出最终结论。"
        else:
            task = "请基于前面的讨论发表你的观点（认同、补充或反驳，并说明理由）。"
        suffix = (
            f"\n\n[工作流 {ctx.idx+1}/{ctx.total}] 讨论第 {rnd}/{total} 轮，轮到你（{bot['name']}）发言。\n{task}"
        )
        return WorkUnit(
            bot=bot,
            executor_id=bot.get("executor_id", "tool_loop_v1"),
            trigger_msg=f"第 {rnd} 轮发言（{bot['name']}）。",
            prompt_suffix=suffix,
        )


class VerificationStage(StageType):
    """验证阶段：多 bot 对同一任务各出方案 → 互相投票 → 票高者的方案胜出并带着它推进。

    两阶段流转：
      propose: 全员并行产出方案（每人一份，针对同一任务）
      vote:    全员阅读所有方案并投票，计票后选出最优
    与 pool（认领各自不同任务）、discussion（串行轮流发言）机制都不同。
    （插拔验证用：新增本类完全不需要改 declarative.py 编排器核心。）
    """
    name = "verification"

    def current_pool_bots(self, stage: dict) -> list[int] | None:
        pending = stage.get("pending")
        if pending is not None:
            return list(pending)
        return [b["id"] for b in stage["bots"]]

    def display_name(self, stage: dict) -> str:
        return stage.get("name", "验证小组")

    def enter(self, ctx: StageCtx, prev_output: str) -> OrchestratorStep:
        stage = ctx.stage
        bots = stage["bots"]
        stage["phase"] = "propose"
        stage["pending"] = [b["id"] for b in bots]
        stage["proposals"] = {}
        stage["votes"] = {}

        step = OrchestratorStep(broadcast_state=True)
        names = "、".join(b["name"] for b in bots)
        step.announcements.append(SystemMessage(
            f"🔬 验证开始：{len(bots)} 位成员针对同一任务各自给出方案 —— {names}", bots[0]["id"]))
        for bot in bots:
            step.next_units.append(self._propose_unit(ctx, bot))
        return step

    def observe(self, ctx: StageCtx, bot_id: int, response: str) -> OrchestratorStep:
        if ctx.stage.get("phase") == "vote":
            return self._observe_vote(ctx, bot_id, response)
        return self._observe_propose(ctx, bot_id, response)

    def _observe_propose(self, ctx: StageCtx, bot_id: int, response: str) -> OrchestratorStep:
        stage = ctx.stage
        pending = stage.get("pending", [])
        if bot_id is None or bot_id not in pending:
            return OrchestratorStep()
        pending.remove(bot_id)
        stage.setdefault("proposals", {})[bot_id] = response
        if pending:
            return OrchestratorStep(broadcast_state=True)
        return self._begin_vote(ctx)  # 全员出完方案 → 进入投票

    def _begin_vote(self, ctx: StageCtx) -> OrchestratorStep:
        stage = ctx.stage
        bots = stage["bots"]
        stage["phase"] = "vote"
        stage["pending"] = [b["id"] for b in bots]

        step = OrchestratorStep(broadcast_state=True)
        step.announcements.append(SystemMessage(
            "🗳️ 方案已全部提交，进入投票：请各自选出你认为最优的方案。", bots[0]["id"]))
        for bot in bots:
            step.next_units.append(self._vote_unit(ctx, bot))
        return step

    def _observe_vote(self, ctx: StageCtx, bot_id: int, response: str) -> OrchestratorStep:
        stage = ctx.stage
        pending = stage.get("pending", [])
        if bot_id is None or bot_id not in pending:
            return OrchestratorStep()
        pending.remove(bot_id)
        voted = self._parse_vote(stage, response)
        if voted is not None:
            stage.setdefault("votes", {})[bot_id] = voted
        if pending:
            return OrchestratorStep(broadcast_state=True)
        return self._finish(ctx)  # 全员投完 → 计票 → 推进

    def _finish(self, ctx: StageCtx) -> OrchestratorStep:
        stage = ctx.stage
        bots = stage["bots"]
        votes = stage.get("votes", {})
        tally: dict[int, int] = {}
        for v in votes.values():
            tally[v] = tally.get(v, 0) + 1
        winner_id = max(tally, key=lambda k: tally[k]) if tally else bots[0]["id"]
        winner = next((b for b in bots if b["id"] == winner_id), bots[0])
        stage["winner"] = winner_id
        winning_text = stage.get("proposals", {}).get(winner_id, "")

        step = OrchestratorStep(broadcast_state=True)
        step.announcements.append(SystemMessage(
            f"🏆 投票结束，**{winner['name']}** 的方案胜出（{tally.get(winner_id, 0)} 票）。", winner_id))
        adv = ctx.orch._advance(ctx.group_id, prev_output=winning_text)
        step.next_units.extend(adv.next_units)
        step.announcements.extend(adv.announcements)
        step.done = adv.done
        return step

    def _parse_vote(self, stage: dict, response: str) -> int | None:
        m = re.search(r'投票[：:]\s*(.+)', response)
        scope = m.group(1) if m else response
        for bot in stage["bots"]:
            if bot["name"] in scope:
                return bot["id"]
        return None

    def rehydrate(self, stage: dict) -> None:
        for key in ("proposals", "votes"):
            d = stage.get(key)
            if isinstance(d, dict):
                stage[key] = {int(k): v for k, v in d.items()}

    def resume(self, ctx: StageCtx) -> list:
        stage = ctx.stage
        vote_phase = stage.get("phase") == "vote"
        units = []
        for bot_id in stage.get("pending", []):
            bot = next((b for b in stage["bots"] if b["id"] == bot_id), None)
            if not bot:
                continue
            units.append(self._vote_unit(ctx, bot) if vote_phase else self._propose_unit(ctx, bot))
        return units

    def snapshot(self, stage: dict) -> dict:
        bots = stage["bots"]
        return {
            "stage_type": "verification",
            "bots": [{"id": b["id"], "name": b["name"], "avatar_color": b["avatar_color"]}
                     for b in bots],
            "phase": stage.get("phase", "propose"),
            "proposals_in": len(stage.get("proposals", {})),
            "votes_in": len(stage.get("votes", {})),
            "winner": stage.get("winner"),
            "total": len(bots),
        }

    def _propose_unit(self, ctx: StageCtx, bot: dict) -> WorkUnit:
        suffix = (
            f"\n\n[工作流 {ctx.idx+1}/{ctx.total}] 验证·出方案：你和其他成员针对同一任务独立给出各自的完整方案。"
            f"请清晰描述方案与理由（稍后大家会投票选出最优）。"
        )
        return WorkUnit(
            bot=bot,
            executor_id=bot.get("executor_id", "tool_loop_v1"),
            trigger_msg=f"请给出你的方案（{bot['name']}）。",
            prompt_suffix=suffix,
        )

    def _vote_unit(self, ctx: StageCtx, bot: dict) -> WorkUnit:
        names = "、".join(b["name"] for b in ctx.stage["bots"])
        suffix = (
            f"\n\n[工作流 {ctx.idx+1}/{ctx.total}] 验证·投票：请阅读前面所有人的方案（{names}），"
            f"选出你认为最优的一个。在回复末尾用「投票：<名字>」给出选择并说明理由。"
        )
        return WorkUnit(
            bot=bot,
            executor_id=bot.get("executor_id", "tool_loop_v1"),
            trigger_msg=f"请投票（{bot['name']}）。",
            prompt_suffix=suffix,
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
register_stage_type(DiscussionStage())
register_stage_type(VerificationStage())

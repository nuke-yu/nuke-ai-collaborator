"""
core/workflow.py — 工作流门面（编排层的薄封装）

历史上这里既做编排（阶段流转）又做执行（自己 stream / 落库 / 统计 token），
两层焊死在一起。现在执行交给 executors（经 core.runner），编排交给
DeclarativeOrchestrator（core.orchestration），本模块只负责把两者接起来并保留
对外 API（api.workflow / core.orchestrator 仍按旧签名调用）。
"""
from db import get_db, get_messages
from bus import bus
from bus.events import WorkflowUpdate
from core.orchestration import parse_tickets
from core.orchestration import registry as orch_registry
from core.runner import apply_step
from core import workflow_store

# 规范单例：runner 与门面共用同一个编排器实例（同一份状态）
_orch = orch_registry.get("workflow_v1")

# 向后兼容：测试与旧代码直接访问 workflow._state / workflow._parse_tickets
_state = _orch._state
_parse_tickets = parse_tickets


# ── 状态查询 ────────────────────────────────────────────────────────────────

def get(group_id: int) -> dict | None:
    return _orch.get(group_id)


def current_bot(group_id: int) -> dict | None:
    return _orch.current_bot(group_id)


def current_pool_bots(group_id: int) -> list[int] | None:
    return _orch.current_pool_bots(group_id)


def is_workflow_participant(group_id: int, bot_id: int) -> bool:
    """某 bot 是否是当前工作流阶段的在岗参与者。

    崩溃恢复走 sessions._dispatch_recovery（绕过 run_unit / check_and_advance），
    完成后据此判断要不要把产出 observe 进编排器以推进工作流。无活跃工作流时返回 False。
    """
    wb = _orch.current_bot(group_id)
    if wb and wb.get("id") == bot_id:
        return True
    wp = _orch.current_pool_bots(group_id)
    return wp is not None and bot_id in wp


def system_suffix(group_id: int) -> str:
    return _orch.system_suffix(group_id)


def _snapshot(group_id: int) -> dict:
    return _orch.snapshot(group_id)


# ── 生命周期 ────────────────────────────────────────────────────────────────

def start(group_id: int, ordered_stages: list) -> None:
    _orch.begin(group_id, ordered_stages)


def end(group_id: int) -> None:
    _orch.end(group_id)


async def broadcast_state(group_id: int) -> None:
    await bus.publish(WorkflowUpdate(group_id=group_id, **_orch.snapshot(group_id)))
    blob = _orch.serialize(group_id)
    if blob is not None:
        await workflow_store.save_state(
            group_id, getattr(_orch, "orchestrator_id", "workflow_v1"), blob)


# ── 流转（决策交编排器，副作用交 runner） ─────────────────────────────────────

async def check_and_advance(group_id: int, response: str, bot_id: int = None) -> bool:
    s = _orch.get(group_id)
    before = s["current"] if s else None
    step = _orch.observe(group_id, bot_id, response)
    await apply_step(group_id, _orch, step)
    after = _orch.get(group_id)
    after_idx = after["current"] if after else None
    return step.done or (before is not None and before != after_idx)


async def advance(group_id: int) -> bool:
    async with get_db() as db:
        recent = await get_messages(db, group_id, limit=10)
    prev_output = ""
    for m in reversed(recent):
        if m.get("sender_type") == "bot":
            prev_output = m["content"]
            break
    step = _orch.advance(group_id, prev_output)
    await apply_step(group_id, _orch, step)
    return step.done

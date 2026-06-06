"""
core/workflow.py — 工作流门面（编排层的薄封装）

历史上这里既做编排（阶段流转）又做执行（自己 stream / 落库 / 统计 token），
两层焊死在一起。现在执行交给 executors（经 core.runner），编排交给注册表里按
orchestrator_id 选出的 Orchestrator（默认 workflow_v1 / DeclarativeOrchestrator），
本模块只负责把两者接起来并保留对外 API（api.workflow / core.orchestrator 仍按旧
签名调用）。

每个 group 可以挂不同的编排器：start() 时记下 group → orchestrator_id，之后所有
查询/流转都按这张表路由到对应实例（_orch_for）。崩溃恢复时 runner.resume_workflows
读持久化的 orchestrator_id，调 bind() 把映射装回，确保后续 live observe 走对编排器。
"""
from db import get_db, get_messages
from core.orchestration import parse_tickets
from core.orchestration import registry as orch_registry
from core.runner import apply_step, mark_gate_confirmed

# 默认编排器：保留模块级单例供向后兼容（测试/旧代码访问 workflow._orch / _state）。
_orch = orch_registry.get("workflow_v1")
_state = _orch._state
_parse_tickets = parse_tickets

# group_id -> orchestrator_id：start()/bind() 写入，end() 清除。
_group_orch: dict[int, str] = {}


def _orch_for(group_id: int):
    return orch_registry.get(_group_orch.get(group_id, "workflow_v1"))


def bind(group_id: int, orchestrator_id: str) -> None:
    """登记某 group 当前由哪个编排器接管（崩溃恢复 restore 后调用）。"""
    _group_orch[group_id] = orchestrator_id or "workflow_v1"


# ── 状态查询 ────────────────────────────────────────────────────────────────

def get(group_id: int) -> dict | None:
    orch = _orch_for(group_id)
    getter = getattr(orch, "get", None)
    return getter(group_id) if getter else None


def current_bot(group_id: int) -> dict | None:
    return _orch_for(group_id).current_bot(group_id)


def current_pool_bots(group_id: int) -> list[int] | None:
    return _orch_for(group_id).current_pool_bots(group_id)


def is_workflow_participant(group_id: int, bot_id: int) -> bool:
    """某 bot 是否是当前工作流阶段的在岗参与者。

    崩溃恢复走 sessions._dispatch_recovery（绕过 run_unit / check_and_advance），
    完成后据此判断要不要把产出 observe 进编排器以推进工作流。无活跃工作流时返回 False。
    """
    orch = _orch_for(group_id)
    wb = orch.current_bot(group_id)
    if wb and wb.get("id") == bot_id:
        return True
    wp = orch.current_pool_bots(group_id)
    return wp is not None and bot_id in wp


def system_suffix(group_id: int) -> str:
    return _orch_for(group_id).system_suffix(group_id)


def _snapshot(group_id: int) -> dict:
    return _orch_for(group_id).snapshot(group_id)


# ── 生命周期 ────────────────────────────────────────────────────────────────

def start(group_id: int, spec, orchestrator_id: str = "workflow_v1"):
    """登记编排器并 begin，返回首步 OrchestratorStep（含可能的首批 next_units）。

    注意要把返回的 step 交给 apply() 施加副作用——declarative 首阶段由用户驱动，
    begin 只回 broadcast_state；但像 round_robin 这种会在 begin 时就派发首位发言者，
    丢掉这个 step 就永远跑不起来。
    """
    _group_orch[group_id] = orchestrator_id or "workflow_v1"
    return _orch_for(group_id).begin(group_id, spec)


async def apply(group_id: int, step) -> None:
    """把一个 OrchestratorStep 施加副作用（广播 / 落库 / 派发单元）。"""
    await apply_step(group_id, _orch_for(group_id), step)


def end(group_id: int) -> None:
    _orch_for(group_id).end(group_id)
    _group_orch.pop(group_id, None)


# ── 流转（决策交编排器，副作用交 runner） ─────────────────────────────────────

async def check_and_advance(group_id: int, response: str, bot_id: int = None) -> bool:
    orch = _orch_for(group_id)
    step = orch.observe(group_id, bot_id, response)
    await apply_step(group_id, orch, step)
    return step.done


async def confirm(group_id: int, gate_id: str = None) -> bool:
    """人在确认门点了「确认」：让编排器推进过门，并施加副作用（交棒给下一个 bot）。"""
    from core.recap import clear_recap
    await clear_recap(group_id)

    orch = _orch_for(group_id)
    step = orch.confirm(group_id, gate_id)
    await apply_step(group_id, orch, step)
    # 把确认门卡片标记为 confirmed（best-effort，逻辑收在 runner，紧挨建卡片处）
    await mark_gate_confirmed(group_id, gate_id)
    return step.done


async def advance(group_id: int) -> bool:
    async with get_db() as db:
        recent = await get_messages(db, group_id, limit=10)
    prev_output = ""
    for m in reversed(recent):
        if m.get("sender_type") == "bot":
            prev_output = m["content"]
            break
    orch = _orch_for(group_id)
    step = orch.advance(group_id, prev_output)
    await apply_step(group_id, orch, step)
    return step.done

import pytest

from memory.adapters.algorithms import VoyagerCriticEngine
from memory.application.voyager_sandbox import SkillSandbox
from memory.contracts import SkillExecutionPlan


@pytest.mark.asyncio
async def test_sandbox_executes_only_registered_tools_with_verification():
    plan = VoyagerCriticEngine.compile_execution_plan({
        "risk_level": "S1", "trigger": "test", "procedure": ["run"],
        "allowed_tools": ["safe_tool"], "verification": ["ok"],
    })
    sandbox = SkillSandbox()
    sandbox.register("safe_tool", lambda value: value + 1)
    result = await sandbox.execute(
        plan, {"safe_tool": {"value": 1}}, hil_approved=True,
        verify_fn=lambda outputs, _verification: outputs == (2,),
    )
    assert result.succeeded is True


@pytest.mark.asyncio
async def test_sandbox_requires_hil_for_tool_plan():
    plan = VoyagerCriticEngine.compile_execution_plan({
        "risk_level": "S1", "trigger": "test", "procedure": ["run"],
        "allowed_tools": ["safe_tool"], "verification": ["ok"],
    })
    sandbox = SkillSandbox()
    sandbox.register("safe_tool", lambda: "ok")
    with pytest.raises(PermissionError):
        await sandbox.execute(plan)


@pytest.mark.asyncio
async def test_sandbox_invokes_compensating_rollback_after_failed_verification():
    sandbox = SkillSandbox()
    sandbox.register("write", lambda: "created")
    rollback = []
    plan = SkillExecutionPlan("x", ("write",), ("write",), ("check",), True)
    result = await sandbox.execute(
        plan, hil_approved=True, verify_fn=lambda *_: False,
        rollback_fn=lambda outputs: rollback.extend(outputs),
    )
    assert result.succeeded is False
    assert rollback == ["created"]


@pytest.mark.asyncio
async def test_sandbox_recomputes_hil_for_write_tool_even_when_plan_lies():
    sandbox = SkillSandbox()
    sandbox.register("write_file", lambda: "created")
    plan = SkillExecutionPlan("x", ("write_file",), ("write_file",), (), False)
    with pytest.raises(PermissionError):
        await sandbox.execute(plan)

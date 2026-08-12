import pytest

from memory.adapters.algorithms import VoyagerCriticEngine
from memory.application.voyager_sandbox import SkillSandbox


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

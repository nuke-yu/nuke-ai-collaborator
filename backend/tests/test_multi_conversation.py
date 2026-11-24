"""Contracts and routing for the multi_conversation application module."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from memory.domain import MemoryScope
from multi_conversation.context import ConversationContextRequest, assemble_context
from multi_conversation.plugin import run_turn
from multi_conversation.session import SessionStartRequest, start_session


@pytest.mark.asyncio
async def test_context_assembly_uses_memory_ports_and_returns_provenance():
    memory = SimpleNamespace(
        recall=AsyncMock(return_value=SimpleNamespace(rendered_context="remembered"))
    )
    group = SimpleNamespace(
        recall_facts=AsyncMock(return_value=SimpleNamespace(rendered_context="group fact"))
    )
    learning = SimpleNamespace(
        recall_experiences=AsyncMock(return_value=("experience", ["exp:1"])),
        recall_skills=AsyncMock(return_value=("skill", ["skill:1"])),
        resolve_learning_refs=AsyncMock(return_value=("exp:1", "skill:1")),
    )
    request = ConversationContextRequest(
        scope=MemoryScope.bot(
            group_id=7, bot_id=3, actor_id="bot:3", run_id="run-1"
        ),
        query="continue the discussion",
        role="developer",
        history=({"role": "user", "content": "prior turn"},),
        run_id="run-1",
        group_id=7,
        bot_id=3,
        personal_user_id=None,
        memory=memory,
        group_knowledge=group,
        learning=learning,
        personal=None,
        apply_budget=lambda text, contexts: (text + " [bounded]", contexts),
    )

    result = await assemble_context(request)

    assert result.memory == "remembered [bounded]"
    assert result.learned_contexts == ("group fact", "experience", "skill")
    assert result.retrieved_experience_ids == ("exp:1",)
    assert result.retrieved_skill_ids == ("skill:1",)
    assert result.injected_memory_refs == ("exp:1", "skill:1")
    recalled = memory.recall.await_args.args[0]
    assert recalled.query == "continue the discussion"
    assert recalled.metadata["history"] == [{"role": "user", "content": "prior turn"}]


@pytest.mark.asyncio
async def test_session_start_request_persists_session_and_trace():
    interaction = SimpleNamespace(
        create_session=AsyncMock(),
        append_session_event=AsyncMock(),
        update_session_status=AsyncMock(),
    )
    request = SessionStartRequest(
        interaction=interaction,
        session_id="session-1",
        group_id=7,
        bot_id=3,
        user_message="hello",
        session_config={"provider": "deepseek", "model_name": "deepseek-chat"},
        executor_id="tool_loop_v1",
        executor_version="1",
        provider="deepseek",
        model="deepseek-chat",
        system_prompt="You are a coding agent.",
        bot={"id": 3, "traits_json": "[]"},
        tool_schemas=(),
        skills=(),
        ruleset=None,
        permission_mode="default",
        memory_refs=(),
        always_skills=(),
        user_content="hello",
        resuming=False,
        thread_id="thread-1",
        run_id="run-1",
    )

    with (
        patch(
            "sessions.manifest.build_capability_manifest",
            return_value=({"manifest_version": 1}, "sha256:manifest"),
        ),
        patch("memory.application.execution_runs.start_run", new=AsyncMock()) as start_run,
        patch(
            "memory.application.reflexion_service.record_memory_injection",
            new=AsyncMock(return_value="decision-1"),
        ) as record_injection,
    ):
        result = await start_session(request)

    interaction.create_session.assert_awaited_once()
    interaction.append_session_event.assert_awaited_once()
    assert result.capability_manifest == {"manifest_version": 1}
    assert result.manifest_hash == "sha256:manifest"
    assert result.memory_injection_decision_id == "decision-1"
    assert start_run.await_args.kwargs["thread_id"] == "thread-1"
    assert record_injection.await_args.kwargs["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_session_resume_updates_status_without_creating_a_second_session():
    interaction = SimpleNamespace(
        create_session=AsyncMock(),
        append_session_event=AsyncMock(),
        update_session_status=AsyncMock(),
    )
    request = SessionStartRequest(
        interaction=interaction,
        session_id="session-1",
        group_id=7,
        bot_id=3,
        user_message="continue",
        session_config={},
        executor_id="tool_loop_v1",
        executor_version="1",
        provider="deepseek",
        model="deepseek-chat",
        system_prompt="You are a coding agent.",
        bot={"id": 3},
        tool_schemas=(),
        skills=(),
        ruleset=None,
        permission_mode="default",
        memory_refs=(),
        always_skills=(),
        user_content="continue",
        resuming=True,
        thread_id="thread-1",
        run_id="run-1",
    )

    with (
        patch("memory.application.execution_runs.start_run", new=AsyncMock()),
        patch(
            "memory.application.reflexion_service.record_memory_injection",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await start_session(request)

    interaction.update_session_status.assert_awaited_once_with("session-1", "running")
    interaction.create_session.assert_not_awaited()
    assert result.capability_manifest is None
    assert result.manifest_hash is None


@pytest.mark.asyncio
async def test_multi_conversation_plugin_delegates_only_ordinary_turns():
    executor = SimpleNamespace(run=AsyncMock(return_value="done"))
    context = SimpleNamespace(execution_mode="multi_conversation", is_workflow=False)

    result = await run_turn(executor, context)

    assert result == "done"
    executor.run.assert_awaited_once_with(context)
    with pytest.raises(ValueError, match="cannot execute mode"):
        await run_turn(executor, SimpleNamespace(execution_mode="online_task", is_workflow=True))


@pytest.mark.asyncio
async def test_runner_uses_application_mode_before_shared_executor():
    from core import runner

    context = SimpleNamespace(execution_mode="multi_conversation", is_workflow=False)
    unit = SimpleNamespace(executor_id="tool_loop_v1", execution_mode="multi_conversation")
    executor = SimpleNamespace(run=AsyncMock(return_value="executor-result"))
    with (
        patch.object(runner.exec_registry, "get", return_value=executor),
        patch("multi_conversation.plugin.run_turn", new=AsyncMock(return_value="conversation-result")) as run_turn_mock,
    ):
        result = await runner._execute_work_unit(unit, context)
        assert result == "conversation-result"
        run_turn_mock.assert_awaited_once_with(executor, context)
        executor.run.assert_not_awaited()

        for mode in ("orchestrated", "online_task"):
            unit.execution_mode = mode
            context.execution_mode = mode
            assert await runner._execute_work_unit(unit, context) == "executor-result"

    assert executor.run.await_count == 2


@pytest.mark.asyncio
async def test_ordinary_chat_and_online_task_select_distinct_execution_modes():
    from core.orchestration.declarative import DeclarativeOrchestrator
    from online_tasks.workflow import CodingAgentOrchestrator

    chat = DeclarativeOrchestrator()
    bot = {"id": 3, "name": "Dev", "role": "developer", "type": "bot"}
    with patch("core.orchestration.declarative.locks.set_active_bot", new=AsyncMock()):
        chat_step = await chat.dispatch(7, {"content": "@Dev hello"}, [bot], [])
    assert chat_step.next_units[0].execution_mode == "multi_conversation"

    coding = CodingAgentOrchestrator()
    online_step = coding.begin(8, {"bots": [bot], "task_id": "story-8"})
    assert online_step.next_units[0].execution_mode == "online_task"
    assert online_step.next_units[0].executor_id == "tool_loop_v1"

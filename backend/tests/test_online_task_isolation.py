"""Dependency and routing boundaries between online tasks and interactive groups."""
import ast
from pathlib import Path

import pytest

import db
from core.orchestration import registry
from core.orchestration.pipeline import build_rd_pipeline


def test_shared_runtime_only_imports_online_tasks_at_workflow_plugin_entry():
    backend = Path(__file__).resolve().parents[1]
    entry = backend / "core/orchestration/plugins/coding_agent.py"
    for directory in ("core", "runtime", "memory", "sessions", "executors"):
        for path in (backend / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_bytes())):
                if isinstance(node, ast.ImportFrom):
                    modules = [f"{node.module}.{name.name}" for name in node.names]
                elif isinstance(node, ast.Import):
                    modules = [name.name for name in node.names]
                else:
                    continue
                for module in modules:
                    if module.split(".")[0] == "online_tasks":
                        assert (path, module) == (entry, "online_tasks.workflow.CodingAgentOrchestrator")
                    assert module.split(".")[:2] != ["plugins", "agent_dashboard"], path


@pytest.mark.asyncio
async def test_online_task_missing_pr_does_not_interrupt_chat_or_role_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_registry", {})
    coding = registry.get("coding_agent_v1")
    interactive = registry.get("workflow_v1")
    ba, dev, qa = [
        {"name": name, "role": name, "type": "bot", "id": number}
        for number, name in enumerate(("BA", "Dev", "QA"), 1)
    ]
    chat_group, team_group, coding_group = 10, 20, 30
    interactive.begin(team_group, build_rd_pipeline(ba, dev, qa))
    coding.begin(coding_group, {"bots": [dev], "task_id": "task_30", "require_pull_request": True})
    done = [{"name": "signal_stage_done", "arguments": {"reason": "ready"}}]

    with db.bind_db(str(tmp_path / "chat.db")):
        async with db.connect() as conn:
            await conn.execute("CREATE TABLE group_locks (group_id INTEGER PRIMARY KEY, bot_id INTEGER, locked_at TEXT)")
            await conn.commit()
        first = await interactive.dispatch(chat_group, {"content": "@QA hello"}, [ba, qa], [])
        assert [unit.bot for unit in first.next_units] == [qa]

        paused = coding.observe(coding_group, dev["id"], "", signals=done)
        assert paused.workflow_paused.reason == "pull_request_missing"
        gate = interactive.observe(team_group, ba["id"], "", signals=done)
        handoff = interactive.confirm(team_group, gate.confirm_gate["gate_id"])
        assert [unit.bot["id"] for unit in handoff.next_units] == [dev["id"]]

        followup = await interactive.dispatch(chat_group, {"content": "go on"}, [ba, qa], [])
        assert [unit.bot for unit in followup.next_units] == [qa]
        assert not followup.next_units[0].is_workflow
        assert coding.serialize(coding_group)["task_id"] == "task_30"

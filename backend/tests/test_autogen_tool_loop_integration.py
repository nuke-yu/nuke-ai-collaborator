from types import SimpleNamespace

import pytest

from executors.plugins.tool_loop_v1_helpers import _inject_failure_insight


@pytest.mark.asyncio
async def test_failed_tool_injects_redacted_autogen_insight_once():
    runner = SimpleNamespace(
        ctx=SimpleNamespace(user_message="repair the file"),
        messages=[],
    )

    await _inject_failure_insight(
        runner,
        "read_file",
        "FileNotFoundError: Authorization: Bearer secret-token",
    )
    await _inject_failure_insight(
        runner,
        "read_file",
        "FileNotFoundError: Authorization: Bearer secret-token",
    )

    assert len(runner.messages) == 1
    content = runner.messages[0]["content"]
    assert "path_not_found" in content
    assert "secret-token" not in content
    assert "[Historical failure insight" in content


@pytest.mark.asyncio
async def test_empty_failure_does_not_change_messages():
    runner = SimpleNamespace(
        ctx=SimpleNamespace(user_message="repair the file"),
        messages=[],
    )

    await _inject_failure_insight(runner, "read_file", "")

    assert runner.messages == []

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from executors.plugins.tool_loop_v1_helpers import _maybe_autogen_retry


@pytest.mark.asyncio
async def test_opt_in_retry_retries_only_allowlisted_read_tool():
    runner = SimpleNamespace(
        autogen_retry_policy={
            "tools": ["read_file"],
            "retryable_categories": ["path_not_found"],
            "max_retries": 1,
        }
    )
    dispatch = AsyncMock(side_effect=[("FileNotFoundError: missing", True), ("ok", False)])
    with patch("executors.tool_dispatch.dispatch_tool", dispatch):
        result, is_error = await _maybe_autogen_retry(
            runner, "read_file", {"path": "missing"}, {},
            "FileNotFoundError: missing", True,
        )
    assert (result, is_error) == ("ok", False)
    assert dispatch.await_count == 2


@pytest.mark.asyncio
async def test_retry_policy_does_not_retry_unallowlisted_write_tool():
    runner = SimpleNamespace(
        autogen_retry_policy={"tools": ["read_file"], "max_retries": 1}
    )
    with patch("executors.tool_dispatch.dispatch_tool", new_callable=AsyncMock) as dispatch:
        result, is_error = await _maybe_autogen_retry(
            runner, "write_file", {}, {}, "permission denied", True
        )
    assert is_error is True
    assert result == "permission denied"
    dispatch.assert_not_awaited()

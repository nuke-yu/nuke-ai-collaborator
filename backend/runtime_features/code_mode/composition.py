"""Composition entry points for the Code Mode bounded context."""
from __future__ import annotations

from .adapters import CallbackBashAdapter, WorkspaceAdapter
from .application import CodeModeService, CodeTools
from .domain import CodeModeLimits, CodeModeRejected


def run_code(
    code: str,
    *,
    bot_id: int,
    group_id: int | None,
    session_id: str | None,
    limits: CodeModeLimits = CodeModeLimits(),
    bash_executor=None,
) -> str:
    if group_id is None:
        raise CodeModeRejected("Code Mode 必须绑定 group_id")
    tools = CodeTools(
        WorkspaceAdapter(bot_id=bot_id, group_id=group_id, session_id=session_id),
        CallbackBashAdapter(bash_executor),
    )
    return CodeModeService().run(code, tools, limits)

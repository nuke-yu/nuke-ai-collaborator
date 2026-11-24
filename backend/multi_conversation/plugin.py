"""Runtime entry point for ordinary multi-turn conversations.

This module is the application boundary between a conversation WorkUnit and the
shared Agent executor. The executor still owns the model/tool loop; this entry
point gives conversation-specific lifecycle and context assembly a home without
changing Orchestrator routing or Online Task execution.
"""
from __future__ import annotations

from executors.base import BotExecutor, ExecutionContext, ExecutionResult

from multi_conversation import EXECUTION_MODE


async def run_turn(
    executor: BotExecutor,
    context: ExecutionContext,
) -> ExecutionResult:
    """Run one ordinary conversation turn through the selected Agent executor.

    The mode guard makes accidental routing from Online Task or workflow units
    fail at the boundary instead of silently changing their execution path.
    """
    if context.execution_mode != EXECUTION_MODE:
        raise ValueError(
            f"multi_conversation cannot execute mode {context.execution_mode!r}"
        )
    if context.is_workflow:
        raise ValueError("multi_conversation cannot execute workflow work units")
    return await executor.run(context)

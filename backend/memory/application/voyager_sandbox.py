"""Safe Voyager-style Skill Library execution boundary."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from memory.contracts import SkillExecutionPlan


@dataclass(frozen=True, slots=True)
class SkillRunResult:
    succeeded: bool
    outputs: tuple[Any, ...]
    verification: tuple[str, ...]


class SkillSandbox:
    """Execute only registered Python callables described by a safe plan."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, tool: Callable[..., Any]) -> None:
        if not name or not callable(tool) or name in {"run_shell", "exec", "eval"}:
            raise ValueError("only safe named callables may be registered")
        self._tools[name] = tool

    async def execute(
        self,
        plan: SkillExecutionPlan,
        arguments: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        hil_approved: bool = False,
        verify_fn: Callable[[tuple[Any, ...], tuple[str, ...]], Awaitable[bool] | bool] | None = None,
        rollback_fn: Callable[[tuple[Any, ...]], Awaitable[None] | None] | None = None,
    ) -> SkillRunResult:
        if plan.requires_hil and not hil_approved:
            raise PermissionError("skill execution requires human approval")
        outputs: list[Any] = []
        arguments = arguments or {}
        for tool_name in plan.allowed_tools:
            tool = self._tools.get(tool_name)
            if tool is None:
                raise ValueError(f"unregistered skill tool: {tool_name}")
            result = tool(**dict(arguments.get(tool_name, {})))
            if inspect.isawaitable(result):
                result = await result
            outputs.append(result)
        if verify_fn is not None:
            verified = verify_fn(tuple(outputs), plan.verification)
            if inspect.isawaitable(verified):
                verified = await verified
            if not verified:
                if rollback_fn is not None:
                    rollback_result = rollback_fn(tuple(outputs))
                    if inspect.isawaitable(rollback_result):
                        await rollback_result
                return SkillRunResult(False, tuple(outputs), plan.verification)
        return SkillRunResult(True, tuple(outputs), plan.verification)

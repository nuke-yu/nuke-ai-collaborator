"""Voyager Critic Success Gate Engine (MIT / GPL-3.0 ported algorithm).

Ported from Voyager (MineDojo/Voyager) Automated Environmental Critic:
- Perform environmental verification & task success gating.
- Evaluate execution traces, tool outcomes, and error assertions.
- Support dual-mode validation: fast deterministic rules + deep LLM reflection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CriticResult:
    passed: bool
    score: float
    critique: str
    verification_mode: str = "deterministic_rules"


VOYAGER_CRITIC_SYSTEM_PROMPT = """You are a Voyager Environmental Critic Agent.
Analyze the agent's task description, tool execution history, and environment outputs.
Evaluate whether the task was genuinely completed successfully without unresolved error states.

Return ONLY a JSON object:
{
  "passed": true | false,
  "score": 0.95,
  "critique": "Clear explanation of evaluation and verification state"
}
"""


class VoyagerCriticEngine:
    """Audit-grade Voyager Environmental Critic Engine (Supports LLM Reflection & Rule Fallback)."""

    def evaluate_success(
        self,
        task: str,
        outcome: str,
        tool_records: Sequence[Mapping[str, Any]] = (),
        error_traces: Sequence[str] = (),
    ) -> CriticResult:
        """Deterministic rule-based environmental verification."""
        if outcome != "completed":
            return CriticResult(
                passed=False,
                score=0.0,
                critique="Execution failed to reach completion state.",
                verification_mode="deterministic_rules",
            )

        error_count = sum(1 for rec in tool_records if rec.get("is_error"))
        if error_traces:
            error_count += len(error_traces)

        if error_count == 0:
            return CriticResult(
                passed=True,
                score=1.0,
                critique="Execution completed cleanly with zero tool errors or trace exceptions.",
                verification_mode="deterministic_rules",
            )

        # Check if errors were followed by successful recovery
        last_record_is_error = bool(tool_records and tool_records[-1].get("is_error"))
        if not last_record_is_error:
            return CriticResult(
                passed=True,
                score=0.85,
                critique=f"Execution encountered {error_count} intermediate error(s) but successfully recovered in final steps.",
                verification_mode="deterministic_rules",
            )

        return CriticResult(
            passed=False,
            score=0.4,
            critique="Execution terminated with unhandled error state in final tool record.",
            verification_mode="deterministic_rules",
        )

    async def evaluate_success_with_llm(
        self,
        task: str,
        outcome: str,
        tool_records: Sequence[Mapping[str, Any]] = (),
        error_traces: Sequence[str] = (),
        ai_call_fn: Any = None,
        model: str = "deepseek-chat",
        provider: str = "deepseek",
    ) -> CriticResult:
        """Deep LLM Reflection-based environmental verification."""
        if ai_call_fn is not None:
            formatted_tools = [
                {"name": r.get("name"), "is_error": r.get("is_error"), "result": str(r.get("result"))[:300]}
                for r in tool_records
            ]
            prompt = (
                f"Task Requirement: {task}\n"
                f"Terminal Outcome: {outcome}\n"
                f"Tool Records: {formatted_tools}\n"
                f"Error Traces: {error_traces}\n\n"
                "Critique task execution success and return JSON."
            )

            try:
                res = await ai_call_fn(
                    VOYAGER_CRITIC_SYSTEM_PROMPT,
                    [{"role": "user", "content": prompt}],
                    provider=provider,
                    model=model,
                    temperature=0.1,
                )
                content = res.get("content", "") if isinstance(res, dict) else str(res)
                import json
                match = re.search(r"\{.*\}", content, re.DOTALL)
                if match:
                    obj = json.loads(match.group(0))
                    return CriticResult(
                        passed=bool(obj.get("passed", True)),
                        score=float(obj.get("score", 0.9)),
                        critique=str(obj.get("critique", "LLM Critic Evaluation")),
                        verification_mode="llm_reflection",
                    )
            except Exception:
                pass

        return self.evaluate_success(task, outcome, tool_records, error_traces)

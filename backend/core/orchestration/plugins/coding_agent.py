"""
core/orchestration/plugins/coding_agent.py — Single-bot autonomous orchestrator.

Discovered automatically by Worker processes via registry.discover() scanning
this directory. This ensures coding_agent_v1 is available in every Worker,
not just the Supervisor process.

Lifecycle:
  begin() → immediately dispatches WorkUnit with requirements as trigger
  observe() → checks completion signals before marking done

Unlike the BA→Dev→QA pipeline:
  - No human confirmation gates (fully autonomous)
  - No stage transitions (single stage)
  - Completion requires signal_stage_done tool call or done keyword
"""
import datetime
import logging
import re

from core.orchestration.base import Orchestrator, OrchestratorStep, WorkUnit

log = logging.getLogger(__name__)

# Completion signals: tool call signal_stage_done or text sentinel
_DONE_KEYWORDS = ("[[AGENT_DONE]]", "[[CODING_DONE]]")
_FAIL_KEYWORDS = ("[[AGENT_FAIL]]", "[[CODING_FAIL]]")


class CodingAgentOrchestrator(Orchestrator):
    """Single-bot autonomous orchestrator for coding agent tasks.

    Registered as orchestrator_id="coding_agent_v1".
    """
    orchestrator_id = "coding_agent_v1"

    def __init__(self):
        # group_id → state dict
        self._state: dict[int, dict] = {}

    def begin(self, group_id: int, spec) -> OrchestratorStep:
        """Start the coding agent workflow.

        spec = {"bots": [bot_dict], "requirements": str, "test_command": str}

        Returns WorkUnit immediately so the bot starts without waiting for
        a separate trigger message.
        """
        bots = spec.get("bots", [])
        if not bots:
            return OrchestratorStep(done=True)

        bot = bots[0]
        requirements = spec.get("requirements", "")
        test_command = spec.get("test_command", "")

        self._state[group_id] = {
            "bot": bot,
            "started": True,
            "done": False,
            "start_time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "requirements": requirements,
            "test_command": test_command,
        }

        trigger = f"Please implement the following feature:\n\n{requirements}"
        if test_command:
            trigger += f"\n\nTest command to verify: `{test_command}`"

        return OrchestratorStep(
            broadcast_state=True,
            next_units=[WorkUnit(
                bot=bot,
                executor_id=bot.get("executor_id", "tool_loop_v1"),
                trigger_msg=trigger,
                is_workflow=True,
            )],
        )

    async def dispatch(self, group_id: int, message: dict, members: list, recent: list) -> OrchestratorStep:
        """Handle human follow-up messages during active workflow.

        For the coding agent, follow-up messages are injected as steer
        (the tool_loop polls for them), not as new WorkUnits.
        """
        s = self._state.get(group_id)
        if not s or s.get("done"):
            return OrchestratorStep()
        # Human follow-ups are handled by tool_loop's steer mechanism,
        # not by creating new WorkUnits. Return empty step.
        return OrchestratorStep()

    def observe(self, group_id: int, bot_id: int, response: str,
                signals: list[dict] | None = None) -> OrchestratorStep:
        """Check if the bot's run completed successfully.

        Completion requires one of:
          1. signal_stage_done tool call (in signals list)
          2. [[AGENT_DONE]] or [[CODING_DONE]] sentinel in response text
          3. Explicit success indicators in the response

        If the bot reports failure (signal_rework, [[AGENT_FAIL]], or
        error indicators), the workflow is NOT marked done — it stays
        active for retry.
        """
        s = self._state.get(group_id)
        if not s or s.get("done"):
            return OrchestratorStep()

        # Check for failure signals first
        if self._has_failure_signal(response, signals):
            log.info("coding_agent_v1: group %d reported failure, keeping active", group_id)
            return OrchestratorStep(broadcast_state=True)

        # Check for success signals
        if self._has_success_signal(response, signals):
            s["done"] = True
            self.end(group_id)
            return OrchestratorStep(done=True, broadcast_state=True)

        # No clear signal — keep workflow active (don't auto-complete)
        return OrchestratorStep()

    def _has_success_signal(self, response: str, signals: list[dict] | None) -> bool:
        """Check for explicit completion signals."""
        # Tool call: signal_stage_done
        if signals:
            for sig in signals:
                if sig.get("tool") == "signal_stage_done":
                    return True

        # Text sentinel
        response_upper = response.upper()
        for kw in _DONE_KEYWORDS:
            if kw.replace("[", "").replace("]", "") in response_upper:
                return True

        return False

    def _has_failure_signal(self, response: str, signals: list[dict] | None) -> bool:
        """Check for explicit failure signals."""
        # Tool call: signal_rework
        if signals:
            for sig in signals:
                if sig.get("tool") == "signal_rework":
                    return True

        # Text sentinel
        response_upper = response.upper()
        for kw in _FAIL_KEYWORDS:
            if kw.replace("[", "").replace("]", "") in response_upper:
                return True

        # Error indicators in response
        error_patterns = [
            r"测试失败", r"test.*fail", r"error.*cannot.*fix",
            r"无法修复", r"PR.*创建失败", r"PR.*creation.*failed",
        ]
        for pattern in error_patterns:
            if re.search(pattern, response, re.IGNORECASE):
                return True

        return False

    def end(self, group_id: int) -> None:
        self._state.pop(group_id, None)

    # ── Query ─────────────────────────────────────────────────────────

    def current_bot(self, group_id: int) -> dict | None:
        s = self._state.get(group_id)
        return s["bot"] if s else None

    def system_suffix(self, group_id: int) -> str:
        return ""

    def snapshot(self, group_id: int) -> dict:
        s = self._state.get(group_id)
        if not s:
            return {"active": False}
        return {
            "active": not s.get("done", False),
            "type": "coding_agent",
            "bot": {"id": s["bot"].get("id"), "name": s["bot"].get("name", "")},
            "done": s.get("done", False),
        }

    # ── Persistence / crash recovery ──────────────────────────────────

    def serialize(self, group_id: int) -> dict | None:
        return self._state.get(group_id)

    def restore(self, group_id: int, state: dict) -> None:
        self._state[group_id] = state

    def resume_units(self, group_id: int) -> list:
        s = self._state.get(group_id)
        if not s or s.get("done") or not s.get("started"):
            return []
        bot = s["bot"]
        return [WorkUnit(
            bot=bot,
            executor_id=bot.get("executor_id", "tool_loop_v1"),
            trigger_msg="Resume your coding task (previous run was interrupted). Continue from where you left off.",
            is_workflow=True,
        )]

    def start_time(self, group_id: int) -> str | None:
        s = self._state.get(group_id)
        return s.get("start_time") if s else None

    def parse_spec(self, body: dict, all_bots: dict[int, dict]) -> dict:
        """Parse START_WORKFLOW body into spec.

        body = {"bot_id": int, "requirements": str, "test_command": str}
        Returns spec with "bots" key to pass dispatch_start_workflow validation.
        """
        bot_id = body.get("bot_id")
        bot = all_bots.get(bot_id, {})
        return {
            "bots": [bot] if bot else [],
            "requirements": body.get("requirements", ""),
            "test_command": body.get("test_command", ""),
        }

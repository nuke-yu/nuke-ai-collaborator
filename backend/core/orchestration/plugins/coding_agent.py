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
  - Completion requires signal_stage_done or signal_rework tool call
  - No completion signal → WorkflowPaused(reason="completion_signal_missing")
"""
import datetime
import logging

from core.orchestration.base import Orchestrator, OrchestratorStep, WorkUnit
from core.orchestration.signals import (
    WorkflowSignal,
    SIGNAL_STAGE_DONE,
    SIGNAL_REWORK,
    is_signal_done,
    is_signal_rework,
    has_completion_signal,
)
from bus.events import WorkflowPaused

log = logging.getLogger(__name__)


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
        """Check if the bot's run completed via structured completion signals.

        Completion protocol:
          - signal_stage_done → workflow done
          - signal_rework → workflow stays active (for retry)
          - No completion signal → WorkflowPaused(reason="completion_signal_missing")

        Text heuristics are NOT used. Only WorkflowSignal schema is authoritative.
        """
        s = self._state.get(group_id)
        if not s or s.get("done"):
            return OrchestratorStep()

        # Check for completion signals (using unified WorkflowSignal schema)
        if signals:
            for sig in signals:
                if is_signal_done(sig):
                    s["done"] = True
                    self.end(group_id)
                    return OrchestratorStep(done=True, broadcast_state=True)
                if is_signal_rework(sig):
                    log.info("coding_agent_v1: group %d reported rework needed", group_id)
                    return OrchestratorStep(broadcast_state=True)

        # No completion signal — this is an incomplete run
        # Publish WorkflowPaused to signal the missing completion signal
        log.warning(
            "coding_agent_v1: group %d completed without completion signal, "
            "publishing WorkflowPaused",
            group_id,
        )
        return OrchestratorStep(
            broadcast_state=True,
            workflow_paused=WorkflowPaused(
                group_id=group_id,
                reason="completion_signal_missing",
                details="Bot run completed without calling signal_stage_done or signal_rework",
            ),
        )

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

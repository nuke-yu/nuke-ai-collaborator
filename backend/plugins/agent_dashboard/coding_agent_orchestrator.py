"""
plugins/agent_dashboard/coding_agent_orchestrator.py — Single-bot autonomous orchestrator.

A minimal orchestrator for the coding agent use case: one bot, one stage, no gates.
Leverages the existing workflow infrastructure for:
  - State persistence via workflow_store (Fix #2: task state survives restart)
  - Crash recovery via resume_workflows() (Fix #3: in-flight tasks auto-resume)
  - WorkflowPaused events on AI failures (Fix #4: provider_unavailable triggers pause)

Unlike the BA→Dev→QA pipeline, this orchestrator has:
  - No human confirmation gates (fully autonomous)
  - No stage transitions (single stage)
  - Auto-completion when the bot finishes

Registration:
  Registered as orchestrator_id="coding_agent_v1" in the orchestrator registry.
  The plugin's _dispatch_agent sends START_WORKFLOW with this orchestrator_id.
"""
import datetime
import logging

from core.orchestration.base import Orchestrator, OrchestratorStep, WorkUnit

log = logging.getLogger(__name__)


class CodingAgentOrchestrator(Orchestrator):
    """Single-bot autonomous orchestrator for coding agent tasks.

    Lifecycle:
      begin() → dispatch WorkUnit to the bot
      observe() → when bot finishes, mark workflow done
      No gates, no stage transitions, fully autonomous.
    """
    orchestrator_id = "coding_agent_v1"

    def __init__(self):
        # group_id → {"bot": dict, "started": bool, "done": bool, "start_time": str}
        self._state: dict[int, dict] = {}

    def begin(self, group_id: int, spec) -> OrchestratorStep:
        """Start the coding agent workflow.

        spec = {"bot": bot_dict, "requirements": str, "test_command": str}
        """
        bot = spec.get("bot", {})
        if not bot:
            return OrchestratorStep(done=True)

        self._state[group_id] = {
            "bot": bot,
            "started": False,
            "done": False,
            "start_time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "requirements": spec.get("requirements", ""),
            "test_command": spec.get("test_command", ""),
        }
        return OrchestratorStep(broadcast_state=True)

    async def dispatch(self, group_id: int, message: dict, members: list, recent: list) -> OrchestratorStep:
        """First human/bot message triggers the coding agent run."""
        s = self._state.get(group_id)
        if not s or s.get("started") or s.get("done"):
            return OrchestratorStep()

        s["started"] = True
        bot = s["bot"]
        requirements = s.get("requirements", "")
        test_command = s.get("test_command", "")

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

    def observe(self, group_id: int, bot_id: int, response: str, signals: list[dict] | None = None) -> OrchestratorStep:
        """Bot finished a run. Mark workflow as done."""
        s = self._state.get(group_id)
        if not s or s.get("done"):
            return OrchestratorStep()

        s["done"] = True
        self.end(group_id)
        return OrchestratorStep(done=True, broadcast_state=True)

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
            trigger_msg="Resume your coding task (previous run was interrupted).",
            is_workflow=True,
        )]

    def start_time(self, group_id: int) -> str | None:
        s = self._state.get(group_id)
        return s.get("start_time") if s else None

    def parse_spec(self, body: dict, all_bots: dict[int, dict]) -> dict:
        """Parse workflow start body into spec.

        body = {"bot_id": int, "requirements": str, "test_command": str}
        """
        bot_id = body.get("bot_id")
        bot = all_bots.get(bot_id, {})
        return {
            "bot": bot,
            "requirements": body.get("requirements", ""),
            "test_command": body.get("test_command", ""),
        }

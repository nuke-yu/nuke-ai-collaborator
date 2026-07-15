"""tests/test_agent_progress.py — ProgressAdapter state machine tests.

Verifies the event → progress translation logic:
  - Phase detection from tool_call events
  - Percent calculation
  - Status transitions (running → done/error/stuck/aborted)
  - Phase only advances forward (never backward)
  - File modification tracking
"""
import pytest
import time
from plugins.agent_dashboard.progress import ProgressAdapter, TaskProgress, PHASE_ORDER


@pytest.fixture
def adapter():
    return ProgressAdapter()


@pytest.fixture
def active_adapter(adapter):
    """Adapter with one active task registered."""
    adapter.register_task(group_id=1, task_id="test_task")
    return adapter


class TestTaskRegistration:

    def test_hydrate_restores_active_and_terminal_tasks(self, adapter):
        adapter.hydrate(
            [
                {
                    "task_id": "active",
                    "group_id": 1,
                    "status": "dispatched",
                    "max_iterations": 50,
                    "created_at": "2026-07-15T10:00:00Z",
                    "updated_at": "2026-07-15T10:01:00Z",
                },
                {
                    "task_id": "finished",
                    "group_id": 2,
                    "status": "completed",
                    "max_iterations": 100,
                    "created_at": "2026-07-15T09:00:00Z",
                    "updated_at": "2026-07-15T09:02:00Z",
                },
            ]
        )

        assert adapter._states[1].status == "running"
        assert adapter._states[1].max_iter == 50
        assert adapter._states[1].elapsed_sec == 60
        assert 1 in adapter._active_groups
        assert adapter._states[2].status == "done"
        assert adapter._states[2].percent == 100
        assert 2 not in adapter._active_groups

    def test_register_task(self, adapter):
        state = adapter.register_task(1, "task_1")
        assert state.group_id == 1
        assert state.task_id == "task_1"
        assert state.phase == "queued"
        assert state.percent == 0
        assert state.status == "running"
        assert 1 in adapter._active_groups

    def test_remove_task(self, adapter):
        """remove_task completely removes task from tracking (rollback scenario)."""
        adapter.register_task(1, "task_1")
        adapter.remove_task(1)
        assert 1 not in adapter._active_groups
        assert 1 not in adapter._states

    def test_mark_aborted(self, adapter):
        """mark_aborted marks task as aborted and removes from active."""
        adapter.register_task(1, "task_1")
        adapter.mark_aborted(1)
        assert 1 not in adapter._active_groups
        assert adapter._states[1].status == "aborted"

    def test_mark_succeeded(self, adapter):
        """mark_succeeded marks task as done with 100% progress."""
        adapter.register_task(1, "task_1")
        adapter.mark_succeeded(1)
        assert 1 not in adapter._active_groups
        assert adapter._states[1].status == "done"
        assert adapter._states[1].phase == "done"
        assert adapter._states[1].percent == 100

    def test_reset_for_retry(self, adapter):
        """reset_for_retry resets state but keeps tracking active."""
        adapter.register_task(1, "task_1")
        # Advance to coding phase
        adapter.on_event(1, {"type": "tool_call", "tool_name": "write_file", "tool_input": {"file_path": "api.py"}})
        assert adapter._states[1].phase == "coding"

        # Reset for retry
        adapter.reset_for_retry(1)
        assert 1 in adapter._active_groups  # Still active
        assert adapter._states[1].phase == "queued"
        assert adapter._states[1].percent == 0
        assert adapter._states[1].status == "running"

    def test_reset_for_retry_reactivates_terminal_task(self, adapter):
        adapter.register_task(1, "task_1")
        adapter.mark_aborted(1)
        assert 1 not in adapter._active_groups

        adapter.reset_for_retry(1)
        assert 1 in adapter._active_groups
        assert adapter._states[1].status == "running"

    def test_get_progress(self, active_adapter):
        progress = active_adapter.get_progress(1)
        assert progress is not None
        assert progress["group_id"] == 1
        assert progress["task_id"] == "test_task"

    def test_get_progress_nonexistent(self, adapter):
        assert adapter.get_progress(999) is None

    def test_get_all_active(self, adapter):
        adapter.register_task(1, "t1")
        adapter.register_task(2, "t2")
        all_active = adapter.get_all_active()
        assert len(all_active) == 2


class TestPhaseDetection:

    def test_explore_tools_advance_to_exploring(self, active_adapter):
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "read_file", "tool_input": {"file_path": "main.py"}})
        state = active_adapter._states[1]
        assert state.phase == "exploring"
        assert "main.py" in state.detail

    def test_code_tools_advance_to_coding(self, active_adapter):
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "write_file", "tool_input": {"file_path": "api.py"}})
        state = active_adapter._states[1]
        assert state.phase == "coding"
        assert "api.py" in state.files_modified

    def test_test_command_advances_to_testing(self, active_adapter):
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "run_shell", "tool_input": "pytest -x tests/"})
        state = active_adapter._states[1]
        assert state.phase == "testing"

    def test_non_test_shell_stays_in_current_phase(self, active_adapter):
        # First advance to coding
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "write_file", "tool_input": {"file_path": "x.py"}})
        # Then run a non-test shell command
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "run_shell", "tool_input": "git status"})
        state = active_adapter._states[1]
        # Should still be in coding (shell didn't match test keywords)
        assert state.phase == "coding"

    def test_create_pr_advances_to_creating_pr(self, active_adapter):
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "create_pr", "tool_input": {}})
        state = active_adapter._states[1]
        assert state.phase == "creating_pr"

    def test_phase_only_advances_forward(self, active_adapter):
        """Phase never goes backward."""
        # Advance to coding
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "write_file", "tool_input": {"file_path": "x.py"}})
        assert active_adapter._states[1].phase == "coding"

        # Try to go back to exploring
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "read_file", "tool_input": {"file_path": "y.py"}})
        # Should still be in coding (exploring < coding, so no backward)
        assert active_adapter._states[1].phase == "coding"


class TestStatusTransitions:

    def test_stream_end_does_not_complete_task(self, active_adapter):
        """stream_end only indicates model output finished, NOT task completion."""
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "create_pr", "tool_input": {}})
        active_adapter.on_event(1, {"type": "stream_end", "preview": "done"})
        state = active_adapter._states[1]
        # Task stays in creating_pr phase - only workflow_update(done=True) completes it
        assert state.phase == "creating_pr"
        assert state.status == "running"

    def test_stream_aborted_sets_aborted(self, active_adapter):
        active_adapter.on_event(1, {"type": "stream_aborted"})
        assert active_adapter._states[1].status == "aborted"
        assert 1 not in active_adapter._active_groups

    def test_stream_error_sets_error(self, active_adapter):
        active_adapter.on_event(1, {"type": "stream_error", "message": "API rate limit"})
        state = active_adapter._states[1]
        assert state.status == "error"
        assert "API rate limit" in state.error_message
        assert 1 not in active_adapter._active_groups

    def test_workflow_done_completes_task(self, active_adapter):
        active_adapter.on_event(1, {"type": "workflow_update", "done": True})
        state = active_adapter._states[1]
        assert state.phase == "done"
        assert state.status == "done"
        assert state.percent == 100
        assert 1 not in active_adapter._active_groups

    def test_workflow_paused_gate(self, active_adapter):
        active_adapter.on_event(1, {"type": "workflow_paused", "reason": "gate"})
        assert active_adapter._states[1].status == "paused"

    def test_workflow_provider_unavailable(self, active_adapter):
        active_adapter.on_event(1, {"type": "workflow_paused", "reason": "provider_unavailable", "details": "DeepSeek down"})
        state = active_adapter._states[1]
        assert state.status == "error"
        assert "DeepSeek down" in state.error_message

    @pytest.mark.parametrize(
        "reason,details",
        [
            ("completion_signal_missing", "No completion signal"),
            ("rework_requested", "Tests are still failing"),
        ],
    )
    def test_workflow_failure_reason_is_terminal(self, active_adapter, reason, details):
        active_adapter.on_event(
            1,
            {"type": "workflow_paused", "reason": reason, "details": details},
        )

        state = active_adapter._states[1]
        assert state.status == "error"
        assert state.error_message == details
        assert 1 not in active_adapter._active_groups


class TestIterationTracking:

    def test_thought_start_updates_iteration(self, active_adapter):
        active_adapter.on_event(1, {"type": "ai_thought_start", "iteration": 5})
        assert active_adapter._states[1].iteration == 5

    def test_percent_increases_with_phase_and_iteration(self, active_adapter):
        state = active_adapter._states[1]
        initial = state.percent

        # Advance to exploring
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "read_file", "tool_input": "x"})
        assert state.percent >= initial

        # Advance to coding
        prev = state.percent
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "write_file", "tool_input": "x"})
        assert state.percent >= prev


class TestFileTracking:

    def test_files_modified_tracked(self, active_adapter):
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "write_file", "tool_input": {"file_path": "src/api.py"}})
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "edit_file", "tool_input": {"file_path": "src/utils.py"}})
        state = active_adapter._states[1]
        assert "src/api.py" in state.files_modified
        assert "src/utils.py" in state.files_modified

    def test_tools_called_tracked(self, active_adapter):
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "read_file", "tool_input": {}})
        active_adapter.on_event(1, {"type": "tool_call", "tool_name": "write_file", "tool_input": {}})
        state = active_adapter._states[1]
        assert "read_file" in state.tools_called
        assert "write_file" in state.tools_called


class TestIgnoreNonActiveGroups:

    def test_events_for_non_active_groups_ignored(self, adapter):
        # No tasks registered
        adapter.on_event(99, {"type": "tool_call", "tool_name": "write_file", "tool_input": "x"})
        assert 99 not in adapter._states

"""Deterministic task verification adapters for Memory outcome evidence."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.domain import OutcomeStatus, evaluate_outcome_verdict


class OutcomeAdapterTest(unittest.TestCase):
    def test_arbitrary_successful_tools_do_not_verify_task_completion(self) -> None:
        verdict = evaluate_outcome_verdict(
            terminal_outcome="completed",
            tool_records=[
                {
                    "name": "write_file",
                    "args": {"path": "app.py"},
                    "result": "written",
                    "is_error": False,
                },
                {
                    "name": "run_shell",
                    "args": {"cmd": "ls"},
                    "result": "app.py",
                    "is_error": False,
                },
            ],
        )

        self.assertEqual(verdict.status, OutcomeStatus.UNVERIFIED_COMPLETION)
        self.assertFalse(verdict.is_verified)
        self.assertEqual(
            [signal.adapter for signal in verdict.signals],
            ["file_change", "shell_exit"],
        )

    def test_pytest_success_is_task_level_verification(self) -> None:
        verdict = evaluate_outcome_verdict(
            terminal_outcome="completed",
            tool_records=[
                {
                    "name": "run_shell",
                    "args": {"cmd": "python3 -m pytest tests/test_api.py -q"},
                    "result": "12 passed",
                    "is_error": False,
                    "step_id": "run:1:step:4",
                }
            ],
        )

        self.assertEqual(verdict.status, OutcomeStatus.VERIFIED_SUCCESS)
        self.assertEqual(verdict.primary_adapter, "pytest")
        self.assertEqual(verdict.signals[0].target, "pytest:tests/test_api.py")

    def test_latest_task_verifier_controls_terminal_verdict(self) -> None:
        verdict = evaluate_outcome_verdict(
            terminal_outcome="completed",
            tool_records=[
                {
                    "name": "run_shell",
                    "args": {"cmd": "npm run build"},
                    "result": "built",
                    "is_error": False,
                },
                {
                    "name": "run_shell",
                    "args": {"cmd": "npm run lint"},
                    "result": "lint failed",
                    "is_error": True,
                },
            ],
        )

        self.assertEqual(verdict.status, OutcomeStatus.VERIFIED_FAILURE)
        self.assertEqual(verdict.primary_adapter, "lint")

    def test_structured_api_and_workflow_results_are_verified(self) -> None:
        api = evaluate_outcome_verdict(
            terminal_outcome="completed",
            tool_records=[
                {
                    "name": "http_request",
                    "args": {"url": "/health"},
                    "result": {"status_code": 204},
                    "is_error": False,
                }
            ],
        )
        workflow = evaluate_outcome_verdict(
            terminal_outcome="completed",
            tool_records=[
                {
                    "name": "signal_stage_done",
                    "args": {"stage": "qa"},
                    "result": "done",
                    "is_error": False,
                }
            ],
        )

        self.assertEqual(api.status, OutcomeStatus.VERIFIED_SUCCESS)
        self.assertEqual(api.primary_adapter, "api_response")
        self.assertEqual(workflow.status, OutcomeStatus.VERIFIED_SUCCESS)

    def test_terminal_cancellation_wins_over_successful_signal(self) -> None:
        verdict = evaluate_outcome_verdict(
            terminal_outcome="cancelled",
            tool_records=[
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest"},
                    "result": "1 passed",
                    "is_error": False,
                }
            ],
        )

        self.assertEqual(verdict.status, OutcomeStatus.CANCELLED)
        self.assertFalse(verdict.is_verified)

    def test_correction_requires_same_target_retry_and_intervening_action(self) -> None:
        verdict = evaluate_outcome_verdict(
            terminal_outcome="completed",
            tool_records=[
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest tests/test_api.py -q"},
                    "result": "1 failed",
                    "is_error": True,
                },
                {
                    "name": "edit_file",
                    "args": {"path": "api.py"},
                    "result": "edited",
                    "is_error": False,
                },
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest tests/test_api.py -q"},
                    "result": "1 passed",
                    "is_error": False,
                },
            ],
        )

        self.assertEqual(verdict.status, OutcomeStatus.VERIFIED_SUCCESS)
        self.assertIsNotNone(verdict.correction)
        self.assertEqual(verdict.correction.target, "pytest:tests/test_api.py")
        self.assertEqual(verdict.correction.corrective_signal_indices, (1,))

    def test_success_after_failure_without_correction_is_not_corrected(self) -> None:
        verdict = evaluate_outcome_verdict(
            terminal_outcome="completed",
            tool_records=[
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest tests/test_api.py"},
                    "result": "transient failure",
                    "is_error": True,
                },
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest tests/test_api.py"},
                    "result": "1 passed",
                    "is_error": False,
                },
            ],
        )

        self.assertEqual(verdict.status, OutcomeStatus.VERIFIED_SUCCESS)
        self.assertIsNone(verdict.correction)

    def test_file_change_after_verification_invalidates_success(self) -> None:
        verdict = evaluate_outcome_verdict(
            terminal_outcome="completed",
            tool_records=[
                {
                    "name": "run_shell",
                    "args": {"cmd": "pytest tests/test_api.py"},
                    "result": "1 passed",
                    "is_error": False,
                },
                {
                    "name": "edit_file",
                    "args": {"path": "api.py"},
                    "result": "edited after test",
                    "is_error": False,
                },
            ],
        )

        self.assertEqual(verdict.status, OutcomeStatus.UNVERIFIED_COMPLETION)
        self.assertIsNone(verdict.correction)


if __name__ == "__main__":
    unittest.main()

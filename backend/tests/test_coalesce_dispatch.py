"""Coalesce-latest (option B): a human follow-up aborts the bot's in-progress
free-form run for the group so the bot answers the LATEST message instead of
stacking replies. Workflow stages and bot/system senders are NOT interrupted.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.dispatch import _coalesce_should_abort
from core.orchestration.base import WorkUnit


def _unit(is_workflow=False):
    return WorkUnit(bot={"id": 1, "name": "Dev"}, is_workflow=is_workflow)


class TestCoalesceShouldAbort(unittest.TestCase):
    def test_human_freeform_aborts_inflight(self):
        self.assertTrue(_coalesce_should_abort({"type": "human"}, [_unit()]))

    def test_workflow_units_not_aborted(self):
        # An active workflow stage must not be torn down mid-pipeline.
        self.assertFalse(_coalesce_should_abort({"type": "human"}, [_unit(is_workflow=True)]))

    def test_non_human_sender_not_aborted(self):
        # Bot-to-bot / system messages don't interrupt an in-progress run.
        self.assertFalse(_coalesce_should_abort({"type": "bot"}, [_unit()]))

    def test_no_units_no_abort(self):
        self.assertFalse(_coalesce_should_abort({"type": "human"}, []))


if __name__ == "__main__":
    unittest.main()

"""Ownership and authority policy for canonical Group Facts."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.domain import FactAuthority, FactSensitivity, admit_group_fact


class GroupFactAdmissionTest(unittest.TestCase):
    def test_only_authoritative_sources_activate_group_facts(self) -> None:
        for source_type, authority in (
            ("user_explicit", FactAuthority.USER_EXPLICIT),
            ("authoritative_project_source", FactAuthority.PROJECT_AUTHORITATIVE),
            ("deterministic_system_state", FactAuthority.SYSTEM_DETERMINISTIC),
        ):
            admission = admit_group_fact(source_type, FactSensitivity.GROUP)
            self.assertTrue(admission.can_activate)
            self.assertEqual(admission.status, "active")
            self.assertEqual(admission.authority, authority)

    def test_bot_claims_remain_provisional(self) -> None:
        for source_type in ("bot_observation", "bot_reply", "bot_inference"):
            admission = admit_group_fact(source_type, FactSensitivity.GROUP)
            self.assertFalse(admission.can_activate)
            self.assertEqual(admission.status, "provisional")

    def test_private_and_secret_content_cannot_enter_group_facts(self) -> None:
        for sensitivity in (FactSensitivity.PRIVATE, FactSensitivity.SECRET):
            with self.assertRaisesRegex(ValueError, "cannot enter Group Facts"):
                admit_group_fact("user_explicit", sensitivity)

    def test_unknown_source_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            admit_group_fact("model_generated", FactSensitivity.GROUP)


if __name__ == "__main__":
    unittest.main()

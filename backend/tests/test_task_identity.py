"""Structured task identity tests."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.domain import identify_task


class TaskIdentityTest(unittest.TestCase):
    def test_exact_signature_remains_text_sensitive(self) -> None:
        first = identify_task("Fix DB migration issue 123")
        second = identify_task("repair database migration issue 456")

        self.assertNotEqual(first.exact_signature, second.exact_signature)
        self.assertEqual(first.semantic_cluster_key, second.semantic_cluster_key)
        self.assertEqual(first.family, "repair")
        self.assertEqual(first.concepts, ("database", "migration"))

    def test_chinese_and_english_aliases_share_cluster(self) -> None:
        english = identify_task("debug API authentication")
        chinese = identify_task("排查 API 认证问题")

        self.assertEqual(english.semantic_cluster_key, chinese.semantic_cluster_key)
        self.assertEqual(english.concepts, ("api", "authentication"))

    def test_distinct_concepts_do_not_collapse(self) -> None:
        database = identify_task("fix database migration")
        frontend = identify_task("fix frontend UI")

        self.assertNotEqual(
            database.semantic_cluster_key, frontend.semantic_cluster_key
        )


if __name__ == "__main__":
    unittest.main()

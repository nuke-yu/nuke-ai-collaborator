from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import workspace
from workspace.observation import get_observation_store


class ReadBeforeMutateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.path = self.root / "sample.txt"
        self.path.write_text("one\ntwo\n", encoding="utf-8")
        get_observation_store().clear_session("session-a")
        get_observation_store().clear_session("session-b")
        self.workspace_patch = patch.object(
            workspace, "_get_effective_ws", return_value=(self.root, "sample.txt")
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        self.workspace_patch.stop()
        self.temp_dir.cleanup()

    async def test_existing_file_requires_observation_before_edit(self) -> None:
        result = await workspace.edit_file(
            7, "sample.txt", "one", "ONE", group_id=3, session_id="session-a"
        )

        self.assertIn("安全拦截", result)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "one\ntwo\n")

    async def test_read_then_edit_succeeds_and_external_change_is_rejected(self) -> None:
        self.assertEqual(
            await workspace.read_file(7, "sample.txt", group_id=3, session_id="session-a"),
            "one\ntwo\n",
        )
        result = await workspace.edit_file(
            7, "sample.txt", "one", "ONE", group_id=3, session_id="session-a"
        )
        self.assertIn("已修改", result)

        self.path.write_text("external\ntwo\n", encoding="utf-8")
        result = await workspace.edit_file(
            7, "sample.txt", "external", "EXTERNAL", group_id=3, session_id="session-a"
        )
        self.assertIn("安全拦截", result)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "external\ntwo\n")

    async def test_observation_isolated_by_session_and_group(self) -> None:
        await workspace.read_file(7, "sample.txt", group_id=3, session_id="session-a")

        other_session = await workspace.edit_file(
            7, "sample.txt", "one", "ONE", group_id=3, session_id="session-b"
        )
        other_group = await workspace.edit_file(
            7, "sample.txt", "one", "ONE", group_id=4, session_id="session-a"
        )

        self.assertIn("安全拦截", other_session)
        self.assertIn("安全拦截", other_group)

    async def test_new_file_can_be_created_and_then_mutated(self) -> None:
        self.path.unlink()
        created = await workspace.write_file(
            7, "sample.txt", "created", group_id=3, session_id="session-a"
        )
        self.assertIn("已写入", created)
        changed = await workspace.edit_file(
            7, "sample.txt", "created", "changed", group_id=3, session_id="session-a"
        )
        self.assertIn("已修改", changed)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "changed")

    async def test_anchored_read_records_version_for_anchored_edit(self) -> None:
        annotated = await workspace.read_anchored(
            7, "sample.txt", group_id=3, session_id="session-a"
        )
        anchor = annotated.splitlines()[0].split("│", 1)[0]
        result = await workspace.edit_anchored(
            7, "sample.txt", [{"anchor": anchor, "op": "replace", "text": "ONE"}],
            group_id=3, session_id="session-a",
        )

        self.assertIn("已修改", result)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "ONE\ntwo\n")


if __name__ == "__main__":
    unittest.main()

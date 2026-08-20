from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from executors.plugins.workspace_tools import _default_output_truncator, _handle_slice_read
from executors.spill import MAX_SLICE_LINES, spill_output


class SpillPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_large_output_is_spilled_with_bounded_locator(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            text = "".join(f"line-{i}\n" for i in range(7000))
            with patch("workspace.group_workspace", return_value=root):
                preview = await _default_output_truncator(
                    "run_shell", {}, text, {"group_id": 12}
                )
                self.assertIsNotNone(preview)
                self.assertIn("spill://tool_result_", preview)
                self.assertNotIn("line-3500", preview)
                self.assertEqual(len(list((root / "truncated_outputs").glob("*.log"))), 1)

    async def test_slice_read_is_group_scoped_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            root = Path(temp).resolve()
            other_root = Path(other).resolve()
            text = "".join(f"line-{i}\n" for i in range(20))
            with patch(
                "workspace.group_workspace",
                side_effect=lambda group_id: root if group_id == 12 else other_root,
            ):
                _preview, locator = spill_output(
                    group_id=12, tool_name="test", text=text, limit=10
                )
                result = await _handle_slice_read(locator, 3, 5, {"group_id": 12})
                self.assertEqual(result, "line-2\nline-3\nline-4\n")
                too_many = await _handle_slice_read(
                    locator, 1, MAX_SLICE_LINES + 1, {"group_id": 12}
                )
                self.assertIn("最多读取", too_many)
                other_group = await _handle_slice_read(locator, 1, 2, {"group_id": 99})
                self.assertIn("不存在", other_group)

    async def test_small_output_does_not_create_spill(self) -> None:
        result = await _default_output_truncator("read_file", {}, "small", {})
        self.assertIsNone(result)

    async def test_slice_read_does_not_load_entire_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            text = "".join(f"line-{i}\n" for i in range(100_000))
            with patch("workspace.group_workspace", return_value=root):
                _preview, locator = spill_output(
                    group_id=12, tool_name="test", text=text, limit=10
                )
                with patch.object(Path, "read_text", side_effect=AssertionError("full read")):
                    result = await _handle_slice_read(locator, 50_000, 50_002, {"group_id": 12})
            self.assertEqual(result, "line-49999\nline-50000\nline-50001\n")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.patch_config import PatchConfigError, apply_patch_file


class PatchConfigTest(unittest.TestCase):
    def _apply(self, content: str, target=None):
        temp = tempfile.NamedTemporaryFile("w", suffix=".yml", encoding="utf-8", delete=False)
        try:
            temp.write(content)
            temp.close()
            return apply_patch_file(temp.name, target=target or SimpleNamespace())
        finally:
            Path(temp.name).unlink(missing_ok=True)

    def test_applies_whitelisted_patch_atomically(self) -> None:
        target = SimpleNamespace(SHELL_EXEC_BACKEND="local")
        report = self._apply(
            "version: 1\nsettings:\n  shell_exec_backend: container\n"
            "  tool_result_max_chars: 30000\n",
            target,
        )
        self.assertEqual(target.SHELL_EXEC_BACKEND, "container")
        self.assertEqual(target.TOOL_RESULT_MAX_CHARS, 30000)
        self.assertEqual(report.applied, ("SHELL_EXEC_BACKEND", "TOOL_RESULT_MAX_CHARS"))

    def test_unknown_duplicate_and_unsafe_values_fail_without_partial_write(self) -> None:
        target = SimpleNamespace(SHELL_EXEC_BACKEND="local")
        with self.assertRaises(PatchConfigError):
            self._apply("version: 1\nsettings:\n  shell_exec_backend: container\n  unknown: 1\n", target)
        self.assertEqual(target.SHELL_EXEC_BACKEND, "local")
        with self.assertRaises(PatchConfigError):
            self._apply("version: 1\nsettings:\n  sandbox:\n    network: host\n", target)
        with self.assertRaises(PatchConfigError):
            self._apply("version: 1\nsettings:\n  tool_result_max_chars: 1\n  tool_result_max_chars: 2\n", target)

    def test_rejects_non_v1_and_non_mapping_payloads(self) -> None:
        with self.assertRaises(PatchConfigError):
            self._apply("version: 2\nsettings: {}\n")
        with self.assertRaises(PatchConfigError):
            self._apply("version: 1\nsettings: []\n")

    def test_missing_file_is_noop(self) -> None:
        self.assertIsNone(apply_patch_file("/tmp/nuke-patch-does-not-exist.yml", target=SimpleNamespace()))

    def test_storage_backend_patch_is_explicitly_selected(self) -> None:
        report = self._apply("version: 1\nsettings:\n  storage_backend: sqlite\n")
        self.assertIn("storage_backend", report.applied)


if __name__ == "__main__":
    unittest.main()

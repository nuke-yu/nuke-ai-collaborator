"""Plan B Task 4 — external layers merge between role and learned."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.composer import merge_layers


def _entry(name, layer, **kw):
    base = {"name": name, "layer": layer, "description": layer, "is_stub": False,
            "fm_keys": [], "status": "active", "path": f"/p/{layer}/{name}.md"}
    base.update(kw)
    return base


class TestExternalMerge(unittest.TestCase):
    def test_external_overrides_role_but_not_learned(self):
        role = [_entry("dup", "role")]
        external = [_entry("dup", "external_global")]
        learned = {"active": [_entry("dup", "learned")]}
        result = merge_layers([], [], role, learned, external=external)
        dup = next(s for s in result if s["name"] == "dup")
        self.assertEqual(dup["layer"], "learned")   # learned still wins

    def test_external_group_overrides_external_global(self):
        external = [_entry("dup", "external_global"), _entry("dup", "external_group")]
        result = merge_layers([], [], [], {}, external=external)
        dup = next(s for s in result if s["name"] == "dup")
        self.assertEqual(dup["layer"], "external_group")

    def test_external_overrides_role_when_no_learned(self):
        role = [_entry("dup", "role")]
        external = [_entry("dup", "external_global")]
        result = merge_layers([], [], role, {}, external=external)
        dup = next(s for s in result if s["name"] == "dup")
        self.assertEqual(dup["layer"], "external_global")

    def test_backward_compatible_without_external(self):
        result = merge_layers([_entry("a", "system")], [], [], {})
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()

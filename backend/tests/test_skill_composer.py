import unittest
from pathlib import Path
from skills.composer import merge_layers


def entry(name, layer, **kw):
    base = {"name": name, "layer": layer, "type": "md", "path": Path(f"/x/{layer}/{name}.md"),
            "description": "", "always": False, "status": "active", "when_to_use": "",
            "learns": False, "is_stub": False, "fm_keys": []}
    base.update(kw)
    return base


class TestComposer(unittest.TestCase):
    def test_later_layer_overrides_and_injected_computed(self):
        out = merge_layers(
            system=[entry("read-file", "system")],
            group=[entry("house", "group", always=True)],
            role=[entry("code-review", "role")],
            learned={"active": [], "personal": {}, "draft": []},
        )
        by = {s["name"]: s for s in out}
        self.assertEqual(by["read-file"]["injected"], "metadata")
        self.assertEqual(by["house"]["injected"], "full")   # always -> full

    def test_system_protected_from_shadow(self):
        out = merge_layers(
            system=[entry("read-file", "system", description="SYS")],
            group=[entry("read-file", "group", description="GROUP")],
            role=[], learned={"active": [], "personal": {}, "draft": []},
        )
        by = {s["name"]: s for s in out}
        self.assertEqual(by["read-file"]["description"], "SYS")  # group cannot shadow system

    def test_disabled_not_injected(self):
        out = merge_layers(
            system=[entry("x", "system", status="disabled")],
            group=[], role=[], learned={"active": [], "personal": {}, "draft": []},
        )
        self.assertIsNone(out[0]["injected"])


if __name__ == "__main__":
    unittest.main()

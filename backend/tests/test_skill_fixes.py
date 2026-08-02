"""Tests for the four skill fixes:

  #4 env-aware sandboxed templating (processor.render_sandboxed)
  #5 cross-layer override diagnostic (discovery._merge_skill_entry)
  #6 mtime-signature scan cache (discovery._list_skills_all_sync)
  #7 always-skill prompt budget / size cap (loader.load_always_skills)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import unittest
from unittest.mock import patch

from skills.processor import render_sandboxed, process_skill_content
from skills.composer import _merge_skill_entry
import skills.constants as skill_constants
import skills.discovery as skill_discovery
import skills.loader as skill_loader
from skills.loader import load_always_skills

_HERE = Path(__file__).parent.parent
_TEST_WS_ROOT = _HERE / "test_ws_skill_fixes"


# --------------------------------------------------------------------------- #
# #4 — sandboxed templating (no fixture needed)
# --------------------------------------------------------------------------- #

class TestSandboxedTemplating(unittest.IsolatedAsyncioTestCase):

    def test_basic_var_substitution(self):
        self.assertEqual(render_sandboxed("os={{ os }}", {"os": "Darwin"}), "os=Darwin")

    def test_conditional(self):
        out = render_sandboxed(
            "{% if is_windows %}powershell{% else %}sh{% endif %}",
            {"is_windows": False},
        )
        self.assertEqual(out, "sh")

    def test_unknown_var_renders_empty(self):
        # undefined var in output position → __str__ returns "" (no crash)
        self.assertEqual(render_sandboxed("x={{ nope }}", {"os": "x"}), "x=")

    def test_no_vars_means_no_render(self):
        # without template_vars the content is returned verbatim (braces kept)
        self.assertEqual(render_sandboxed("{{ os }}", {}), "{{ os }}")

    def test_sandbox_blocks_attribute_escape(self):
        # classic sandbox-escape probe → SecurityError caught → raw returned,
        # never evaluated to a Python object repr.
        payload = "{{ ().__class__.__bases__ }}"
        out = render_sandboxed(payload, {"x": 1})
        self.assertEqual(out, payload)
        self.assertNotIn("object", out)

    def test_globals_cleared_no_builtins(self):
        # range() is a builtin global; cleared env → undefined → call raises →
        # caught → raw returned (definitely not "[0, 1, 2]" / executed).
        payload = "{{ range(3) }}"
        out = render_sandboxed(payload, {"x": 1})
        self.assertEqual(out, payload)

    async def test_process_skill_content_applies_template(self):
        out = await process_skill_content("os={{ os }}", "/tmp", template_vars={"os": "Linux"})
        self.assertIn("os=Linux", out)

    async def test_process_skill_content_without_vars_keeps_literal(self):
        out = await process_skill_content("os={{ os }}", "/tmp")
        self.assertIn("{{ os }}", out)


# --------------------------------------------------------------------------- #
# #5 — cross-layer override diagnostic (no fixture needed)
# --------------------------------------------------------------------------- #

class TestOverrideDiagnostic(unittest.TestCase):

    def _entry(self, layer, path):
        return {"name": "t", "layer": layer, "path": Path(path),
                "is_stub": False, "fm_keys": []}

    def test_nonsystem_override_logs(self):
        merged = {}
        _merge_skill_entry(merged, self._entry("group", "/g/t.md"))
        with self.assertLogs("skills.composer", level="INFO") as cm:
            _merge_skill_entry(merged, self._entry("role", "/r/t.md"))
        self.assertTrue(any("override" in line.lower() for line in cm.output))
        # winner is the later (role) layer
        self.assertEqual(merged["t"]["path"], Path("/r/t.md"))

    def test_first_insert_does_not_log(self):
        merged = {}
        # No prior entry → no override → no diagnostic (assertNoLogs needs 3.10+)
        with self.assertRaises(AssertionError):
            with self.assertLogs("skills.composer", level="INFO"):
                _merge_skill_entry(merged, self._entry("group", "/g/t.md"))


# --------------------------------------------------------------------------- #
# Fixture base for tests that need real skill dirs
# --------------------------------------------------------------------------- #

class _SkillDirFixture(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Patch the canonical source of truth (skills.constants); discovery,
        # loader and the SkillSource classes all read it live.
        self._orig = (skill_constants.WORKSPACE_ROOT, skill_constants.SYSTEM_SKILLS_ROOT,
                      skill_constants.ROLES_ROOT, skill_constants.bot_ws)
        skill_constants.WORKSPACE_ROOT = _TEST_WS_ROOT
        skill_constants.SYSTEM_SKILLS_ROOT = _TEST_WS_ROOT / "system" / "skills"
        skill_constants.ROLES_ROOT = _TEST_WS_ROOT / "roles"
        skill_constants.bot_ws = lambda bot_id, group_id=None: _TEST_WS_ROOT / "bot_ws_1"

        self.test_sys = _TEST_WS_ROOT / "system" / "skills"
        self.test_sys.mkdir(parents=True, exist_ok=True)
        (_TEST_WS_ROOT / "group_1" / "shared" / "skills").mkdir(parents=True, exist_ok=True)
        (_TEST_WS_ROOT / "bot_ws_1" / "skills").mkdir(parents=True, exist_ok=True)
        skill_discovery.invalidate_skills_cache()

    def tearDown(self):
        (skill_constants.WORKSPACE_ROOT, skill_constants.SYSTEM_SKILLS_ROOT,
         skill_constants.ROLES_ROOT, skill_constants.bot_ws) = self._orig
        skill_discovery.invalidate_skills_cache()
        if _TEST_WS_ROOT.exists():
            shutil.rmtree(_TEST_WS_ROOT)


# --------------------------------------------------------------------------- #
# #6 — mtime-signature scan cache
# --------------------------------------------------------------------------- #

class TestScanCache(_SkillDirFixture):

    async def test_cache_hit_skips_recompute(self):
        (self.test_sys / "a.md").write_text("---\nname: a\n---\nbody", encoding="utf-8")
        skill_discovery.invalidate_skills_cache()
        r1 = skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        self.assertTrue(any(s["name"] == "a" for s in r1))

        # No file change → same signature → must NOT recompute.
        with patch.object(skill_discovery, "_compute_skills_all",
                          side_effect=AssertionError("recomputed on cache hit")):
            r2 = skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        self.assertTrue(any(s["name"] == "a" for s in r2))

    async def test_signature_change_triggers_recompute(self):
        (self.test_sys / "a.md").write_text("---\nname: a\n---\nbody", encoding="utf-8")
        skill_discovery.invalidate_skills_cache()
        skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        # Add a new file → signature diverges → fresh scan picks it up.
        (self.test_sys / "b.md").write_text("---\nname: b\n---\nbody", encoding="utf-8")
        r = skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        self.assertTrue(any(s["name"] == "b" for s in r))

    async def test_returned_lists_are_independent_copies(self):
        (self.test_sys / "a.md").write_text("---\nname: a\n---\nbody", encoding="utf-8")
        skill_discovery.invalidate_skills_cache()
        r1 = skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        r1[0]["injected"] = "MUTATED"           # caller mutates its copy
        r2 = skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        self.assertNotEqual(r2[0].get("injected"), "MUTATED")  # cache not poisoned

    async def test_returned_lists_are_deeply_independent_copies(self):
        (self.test_sys / "a.md").write_text(
            "---\nname: a\nallowed_tools:\n  - read_file\n---\nbody", encoding="utf-8"
        )
        skill_discovery.invalidate_skills_cache()
        r1 = skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        r1[0]["allowed_tools"].append("MUTATED_TOOL")  # caller mutates nested list
        r2 = skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        self.assertNotIn("MUTATED_TOOL", r2[0].get("allowed_tools", []))  # cache not poisoned

    async def test_invalidate_forces_recompute(self):
        (self.test_sys / "a.md").write_text("---\nname: a\n---\nbody", encoding="utf-8")
        skill_discovery.invalidate_skills_cache()
        skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
        skill_discovery.invalidate_skills_cache()
        with patch.object(skill_discovery, "_compute_skills_all",
                          wraps=skill_discovery._compute_skills_all) as spy:
            skill_discovery._list_skills_all_sync(bot_id=1, group_id=1)
            spy.assert_called_once()


# --------------------------------------------------------------------------- #
# #7 — always-skill prompt budget / size cap
# --------------------------------------------------------------------------- #

class TestAlwaysSkillBudget(_SkillDirFixture):

    async def test_large_always_skill_degraded_small_kept(self):
        big_body = "x" * 9000  # > _MAX_SKILL_BODY_CHARS (8000)
        (self.test_sys / "big.md").write_text(
            f"---\nname: big\nalways: true\ndescription: a big skill\n---\n{big_body}",
            encoding="utf-8",
        )
        (self.test_sys / "small.md").write_text(
            "---\nname: small\nalways: true\n---\nshort body",
            encoding="utf-8",
        )
        skill_discovery.invalidate_skills_cache()
        loaded = await load_always_skills(bot_id=1, group_id=1)
        by_name = {s["name"]: s for s in loaded}

        self.assertIn("big", by_name)
        self.assertTrue(by_name["big"].get("degraded"))
        self.assertIn("run_skill", by_name["big"]["content"])
        self.assertNotIn(big_body, by_name["big"]["content"])   # full body NOT injected

        self.assertIn("small", by_name)
        self.assertFalse(by_name["small"].get("degraded"))
        self.assertIn("short body", by_name["small"]["content"])
        self.assertEqual(by_name["small"]["evidence_link"]["relation"], "injected")
        self.assertTrue(
            by_name["small"]["evidence_link"]["ref"].startswith(
                "skill:file:system:small@sha256:"
            )
        )

    async def test_total_budget_degrades_overflow(self):
        # Three skills each ~7000 chars (< per-skill cap) but together > 24000
        # total budget → later ones degrade.
        body = "y" * 7000
        for n in ("s1", "s2", "s3", "s4"):
            (self.test_sys / f"{n}.md").write_text(
                f"---\nname: {n}\nalways: true\n---\n{body}", encoding="utf-8"
            )
        skill_discovery.invalidate_skills_cache()
        loaded = await load_always_skills(bot_id=1, group_id=1)
        degraded = [s["name"] for s in loaded if s.get("degraded")]
        full = [s["name"] for s in loaded if not s.get("degraded")]
        self.assertTrue(full, "at least the first skill should fit")
        self.assertTrue(degraded, "later skills should degrade once budget is spent")


if __name__ == "__main__":
    unittest.main()

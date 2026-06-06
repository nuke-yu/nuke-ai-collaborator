import os
import sys
from pathlib import Path

# Add backend directory to sys.path to allow relative imports (like importing core/skills directly)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import unittest

from skills.metadata import parse_frontmatter, parse_skill_meta
from skills.discovery import _list_skills_all_sync
from skills.loader import run_skill, load_always_skills
import skills.discovery as skill_discovery
import skills.loader as skill_loader

_HERE = Path(__file__).parent.parent
_TEST_WS_ROOT = _HERE / "test_ws_skills_a1_a3"


class TestSkillsA1A3(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Save original values
        self._orig_discovery_ws = skill_discovery.WORKSPACE_ROOT
        self._orig_discovery_sys = skill_discovery.SYSTEM_SKILLS_ROOT
        self._orig_discovery_roles = skill_discovery.ROLES_ROOT
        self._orig_discovery_bot_ws = skill_discovery.bot_ws

        self._orig_loader_ws = skill_loader.WORKSPACE_ROOT
        self._orig_loader_sys = skill_loader.SYSTEM_SKILLS_ROOT
        self._orig_loader_roles = skill_loader.ROLES_ROOT
        self._orig_loader_bot_ws = skill_loader.bot_ws

        # Setup test paths
        self.test_sys = _TEST_WS_ROOT / "system" / "skills"
        self.test_roles = _TEST_WS_ROOT / "roles"

        # Apply overrides
        skill_discovery.WORKSPACE_ROOT = _TEST_WS_ROOT
        skill_discovery.SYSTEM_SKILLS_ROOT = self.test_sys
        skill_discovery.ROLES_ROOT = self.test_roles
        skill_discovery.bot_ws = lambda bot_id: _TEST_WS_ROOT / "bot_ws_1"

        skill_loader.WORKSPACE_ROOT = _TEST_WS_ROOT
        skill_loader.SYSTEM_SKILLS_ROOT = self.test_sys
        skill_loader.ROLES_ROOT = self.test_roles
        skill_loader.bot_ws = lambda bot_id: _TEST_WS_ROOT / "bot_ws_1"

        # Create temporary directories
        self.test_sys.mkdir(parents=True, exist_ok=True)
        (_TEST_WS_ROOT / "group_1" / "shared" / "skills").mkdir(parents=True, exist_ok=True)
        (_TEST_WS_ROOT / "bot_ws_1" / "skills").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        # Restore original values
        skill_discovery.WORKSPACE_ROOT = self._orig_discovery_ws
        skill_discovery.SYSTEM_SKILLS_ROOT = self._orig_discovery_sys
        skill_discovery.ROLES_ROOT = self._orig_discovery_roles
        skill_discovery.bot_ws = self._orig_discovery_bot_ws

        skill_loader.WORKSPACE_ROOT = self._orig_loader_ws
        skill_loader.SYSTEM_SKILLS_ROOT = self._orig_loader_sys
        skill_loader.ROLES_ROOT = self._orig_loader_roles
        skill_loader.bot_ws = self._orig_loader_bot_ws

        # Cleanup test workspace
        if _TEST_WS_ROOT.exists():
            shutil.rmtree(_TEST_WS_ROOT)

    def test_yaml_frontmatter_parsing_a2(self):
        # Test 1: Standard comma separated tools
        content_1 = """---
name: test-skill
allowed-tools: tool1, tool2
always: true
---
body here"""
        fm = parse_frontmatter(content_1)
        self.assertEqual(fm.get("name"), "test-skill")
        self.assertEqual(fm.get("allowed_tools"), ["tool1", "tool2"])
        self.assertTrue(fm.get("always"))

        # Test 2: Standard block list format (valid YAML list)
        content_2 = """---
name: test-skill-2
allowed_tools:
  - tool3
  - tool4
always: false
---
body here"""
        fm = parse_frontmatter(content_2)
        self.assertEqual(fm.get("name"), "test-skill-2")
        self.assertEqual(fm.get("allowed_tools"), ["tool3", "tool4"])
        self.assertFalse(fm.get("always"))

    def test_system_shadow_protection_a1(self):
        # Define a system skill (protected)
        sys_skill_path = self.test_sys / "read-file.md"
        sys_skill_path.write_text("""---
name: read-file
always: true
layer: system
---
System read-file body""", encoding="utf-8")

        # Define an overriding personal skill
        bot_skills_dir = _TEST_WS_ROOT / "bot_ws_1" / "skills"
        personal_skill_path = bot_skills_dir / "read-file.md"
        personal_skill_path.write_text("""---
name: read-file
always: true
layer: personal
---
Personal read-file body override""", encoding="utf-8")

        skills = _list_skills_all_sync(bot_id=1, group_id=1)
        # Find read-file in merged list
        read_file_skill = next((s for s in skills if s["name"] == "read-file"), None)
        
        self.assertIsNotNone(read_file_skill)
        # Ensure it is STILL the system layer one (Winner) and has system body path
        self.assertEqual(read_file_skill.get("layer"), "system")
        self.assertEqual(read_file_skill.get("path"), sys_skill_path)

    async def test_personal_stub_fallback_a3(self):
        # Define a group skill
        group_skills_dir = _TEST_WS_ROOT / "group_1" / "shared" / "skills"
        group_skill_path = group_skills_dir / "build-helper.md"
        group_skill_path.write_text("""---
name: build-helper
always: false
layer: group
status: active
allowed-tools: run_shell
---
Group build-helper body""", encoding="utf-8")

        # Define a personal stub override (e.g. status: disabled or status: active override)
        bot_skills_dir = _TEST_WS_ROOT / "bot_ws_1" / "skills"
        personal_stub_path = bot_skills_dir / "build-helper.md"
        personal_stub_path.write_text("""---
name: build-helper
layer: personal
status: active
---""", encoding="utf-8")  # Body is empty (is a stub)

        skills = _list_skills_all_sync(bot_id=1, group_id=1)
        skill_entry = next((s for s in skills if s["name"] == "build-helper"), None)
        
        self.assertIsNotNone(skill_entry)
        self.assertEqual(skill_entry.get("layer"), "personal")
        self.assertEqual(skill_entry.get("status"), "active")
        self.assertTrue(skill_entry.get("is_stub"))
        
        # The body path MUST fallback to the group layer skill path!
        self.assertEqual(skill_entry.get("path"), group_skill_path)
        
        # Verify loader can run and load content correctly
        ctx = {"group_id": 1}
        content = await run_skill(bot_id=1, name="build-helper", ctx=ctx)
        self.assertIn("Group build-helper body", content)
        self.assertEqual(ctx.get("skill_allowed_tools"), ["run_shell"])

    def test_filter_skills_by_context_b1(self):
        from skills.filter import filter_skills_by_context

        # Mock list of skills with different roles and stages specs
        skills = [
            {"name": "dev-skill", "roles": ["dev"], "stages": ["dev"], "when_to_use": "compile or build"},
            {"name": "qa-skill", "roles": ["qa"], "stages": ["qa"], "when_to_use": "test or verify"},
            {"name": "confirm-skill", "stages": ["awaiting_confirm"], "when_to_use": "approve or confirm"},
            {"name": "always-eligible"}
        ]

        # Case 1: bot is 'dev' and stage is 'dev'
        res = filter_skills_by_context(skills, "I want to compile the code", bot_role="developer", current_stage="dev")
        self.assertEqual(len(res), 2)
        names = [s["name"] for s in res]
        self.assertIn("dev-skill", names)
        self.assertIn("always-eligible", names)

        # Case 2: bot is 'qa' and stage is 'qa'
        res = filter_skills_by_context(skills, "I want to test", bot_role="QA Engineer", current_stage="qa")
        self.assertEqual(len(res), 2)
        names = [s["name"] for s in res]
        self.assertIn("qa-skill", names)
        self.assertIn("always-eligible", names)

        # Case 3: bot is 'dev' but stage is 'qa' -> dev-skill is filtered out by stage constraint
        res = filter_skills_by_context(skills, "I want to compile", bot_role="developer", current_stage="qa")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "always-eligible")

        # Case 4: awaiting confirmation stage
        res = filter_skills_by_context(skills, "I want to approve", bot_role="developer", current_stage="dev", is_awaiting_confirm=True)
        self.assertEqual(len(res), 2)
        names = [s["name"] for s in res]
        self.assertIn("confirm-skill", names)
        self.assertIn("always-eligible", names)


if __name__ == "__main__":
    unittest.main()

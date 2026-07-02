import os
import sys
from pathlib import Path

# Add backend directory to sys.path to allow relative imports (like importing core/skills directly)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import unittest
import fcntl
from unittest.mock import patch
from contextlib import nullcontext

from skills.metadata import parse_frontmatter, parse_skill_meta
from skills.discovery import _list_skills_all_sync
from skills.loader import run_skill, load_always_skills
import skills.constants as skill_constants
import skills.discovery as skill_discovery
import skills.loader as skill_loader

_HERE = Path(__file__).parent.parent
_TEST_WS_ROOT = _HERE / "test_ws_skills_a1_a3"


class TestSkillsA1A3(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Save original values — patch the canonical source of truth
        # (skills.constants); discovery/loader/sources all read it live.
        self._orig_ws = skill_constants.WORKSPACE_ROOT
        self._orig_sys = skill_constants.SYSTEM_SKILLS_ROOT
        self._orig_roles = skill_constants.ROLES_ROOT
        self._orig_bot_ws = skill_constants.bot_ws

        # Setup test paths
        self.test_sys = _TEST_WS_ROOT / "system" / "skills"
        self.test_roles = _TEST_WS_ROOT / "roles"

        # Apply overrides on skills.constants (single source of truth)
        skill_constants.WORKSPACE_ROOT = _TEST_WS_ROOT
        skill_constants.SYSTEM_SKILLS_ROOT = self.test_sys
        skill_constants.ROLES_ROOT = self.test_roles
        skill_constants.bot_ws = lambda bot_id, group_id=None: _TEST_WS_ROOT / "bot_ws_1"

        # Create temporary directories
        self.test_sys.mkdir(parents=True, exist_ok=True)
        (_TEST_WS_ROOT / "group_1" / "shared" / "skills").mkdir(parents=True, exist_ok=True)
        (_TEST_WS_ROOT / "bot_ws_1" / "skills").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        # Restore original values on skills.constants
        skill_constants.WORKSPACE_ROOT = self._orig_ws
        skill_constants.SYSTEM_SKILLS_ROOT = self._orig_sys
        skill_constants.ROLES_ROOT = self._orig_roles
        skill_constants.bot_ws = self._orig_bot_ws

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

    def test_regex_naming_whitelist_c3(self):
        from skills.metadata import _is_safe_name
        self.assertTrue(_is_safe_name("valid-name-123_abc"))
        self.assertFalse(_is_safe_name("invalid.name"))
        self.assertFalse(_is_safe_name("invalid/name"))
        self.assertFalse(_is_safe_name("invalid\\name"))
        self.assertFalse(_is_safe_name("invalid name"))
        self.assertFalse(_is_safe_name("UPPERCASE"))

    def test_manual_folder_fallback_b3(self):
        # Create a manual skill in the new manual/ subfolder
        manual_dir = _TEST_WS_ROOT / "bot_ws_1" / "skills" / "manual"
        manual_dir.mkdir(parents=True, exist_ok=True)
        (manual_dir / "my-manual-skill.md").write_text("""---
name: my-manual-skill
layer: personal
always: false
---
Manual skill body""", encoding="utf-8")

        # Create a legacy personal skill directly in the skills/ root
        legacy_dir = _TEST_WS_ROOT / "bot_ws_1" / "skills"
        (legacy_dir / "my-legacy-skill.md").write_text("""---
name: my-legacy-skill
layer: personal
always: false
---
Legacy skill body""", encoding="utf-8")

        # List all skills
        skills = _list_skills_all_sync(bot_id=1, group_id=1)
        names = [s["name"] for s in skills]
        
        # Verify BOTH manual/ and legacy/ root skills are scanned
        self.assertIn("my-manual-skill", names)
        self.assertIn("my-legacy-skill", names)

    def test_draft_diagnostics_c1_c2(self):
        # Setup an active skill that will collide
        sys_skill_path = self.test_sys / "read-file.md"
        sys_skill_path.write_text("""---
name: read-file
always: true
layer: system
---
System read-file body""", encoding="utf-8")

        # Write a draft skill with naming collision and requesting high-privilege tools
        draft_dir = _TEST_WS_ROOT / "bot_ws_1" / "skills" / "learned" / "draft"
        draft_dir.mkdir(parents=True, exist_ok=True)
        
        # This draft is named "read-file" (collides with system skill) and requests "run_shell"
        draft_file = draft_dir / "read-file.md"
        draft_file.write_text("""---
name: read-file
layer: learned
status: draft
allowed-tools: run_shell, other_tool
---
Draft body""", encoding="utf-8")

        skills = _list_skills_all_sync(bot_id=1, group_id=1)
        # Find draft skill in list
        draft_entry = next((s for s in skills if s["name"] == "read-file" and s["status"] == "draft"), None)
        
        self.assertIsNotNone(draft_entry)
        diagnostics = draft_entry.get("diagnostics", [])
        self.assertEqual(len(diagnostics), 2)
        
        diag_types = [d["type"] for d in diagnostics]
        self.assertIn("collision", diag_types)
        self.assertIn("privilege", diag_types)

        # Test 2: Draft that does not declare allowed-tools but mentions "write_file" in its body
        draft_file_2 = draft_dir / "audit-test.md"
        draft_file_2.write_text("""---
name: audit-test
layer: learned
status: draft
---
Please write_file to save the output.""", encoding="utf-8")

        skills = _list_skills_all_sync(bot_id=1, group_id=1)
        draft_entry_2 = next((s for s in skills if s["name"] == "audit-test" and s["status"] == "draft"), None)
        
        self.assertIsNotNone(draft_entry_2)
        diagnostics_2 = draft_entry_2.get("diagnostics", [])
        self.assertEqual(len(diagnostics_2), 1)
        self.assertEqual(diagnostics_2[0]["type"], "privilege")

    def test_file_locking_c4(self):
        from skills.lifecycle import file_lock
        import tempfile
        import hashlib
        
        test_file = _TEST_WS_ROOT / "lock_test.txt"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("content", encoding="utf-8")
        
        abs_path = str(test_file.resolve())
        lock_name = hashlib.sha256(abs_path.encode("utf-8")).hexdigest() + ".lock"
        lock_path = Path(tempfile.gettempdir()) / "nuke_skill_locks" / lock_name
        
        lock_path.unlink(missing_ok=True)
        self.assertFalse(lock_path.exists())
        
        with file_lock(test_file):
            self.assertTrue(lock_path.exists())
            
        # Verify we can acquire it again.
        with file_lock(test_file):
            self.assertTrue(lock_path.exists())

    def test_file_lock_logs_when_release_fails(self):
        from skills.lifecycle import file_lock
        import tempfile

        test_file = _TEST_WS_ROOT / "lock_release_fail.txt"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("content", encoding="utf-8")

        calls = []

        def fake_flock(fd, op):
            calls.append(op)
            if op == fcntl.LOCK_UN:
                raise OSError("unlock failed")

        with patch("fcntl.flock", side_effect=fake_flock), \
             self.assertLogs("skills.lifecycle", level="ERROR") as logs:
            with file_lock(test_file):
                self.assertTrue(calls)

        self.assertIn(fcntl.LOCK_UN, calls)
        self.assertTrue(any("failed to release file lock" in line for line in logs.output))

    def test_update_skill_status_logs_when_active_override_removal_fails(self):
        from skills.lifecycle import update_skill_status

        skill_dir = _TEST_WS_ROOT / "bot_ws_1" / "skills"
        skill_dir.mkdir(parents=True, exist_ok=True)
        override = skill_dir / "demo.md"
        override.write_text("""---
name: demo
layer: personal
status: disabled
---""", encoding="utf-8")

        with patch("skills.lifecycle.skill_path", return_value=(override, "md")), \
             patch("skills.lifecycle.file_lock", return_value=nullcontext()), \
             patch("pathlib.PosixPath.unlink", side_effect=OSError("unlink failed")), \
             self.assertLogs("skills.lifecycle", level="ERROR") as logs:
            result = update_skill_status(bot_id=1, skill_name="demo", new_status="active")

        self.assertIn("demo", result)
        self.assertTrue(any("failed to remove active override" in line for line in logs.output))

    def test_current_stage_role_family_filter_integration(self):
        import core.workflow as wf
        from core.orchestration import registry as orch_registry
        
        group_id = 9999
        wf.end(group_id)
        
        try:
            # Start a stage with name="DevBot" and role="DevOps工程师" (family="dev")
            stages = [
                {
                    "id": 1,
                    "name": "DevBot",
                    "role": "DevOps工程师",
                    "stage_type": "single",
                    "done_keyword": "[[DEV_DONE]]"
                }
            ]
            wf.start(group_id, stages, "workflow_v1")
            
            orch = orch_registry.get("workflow_v1")
            self.assertIsNotNone(orch)
            
            # Verify stage name resolves to its role family "dev"
            current_stage = orch.current_stage_role(group_id)
            self.assertEqual(current_stage, "dev")
            
            # Test integration with filter_skills_by_context
            from skills.filter import filter_skills_by_context
            skills = [
                {"name": "dev-skill", "roles": ["dev"], "stages": ["dev"], "when_to_use": "build"},
                {"name": "qa-skill", "roles": ["qa"], "stages": ["qa"], "when_to_use": "test"}
            ]
            
            res = filter_skills_by_context(skills, "I want to build the code", bot_role="developer", current_stage=current_stage)
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["name"], "dev-skill")
        finally:
            wf.end(group_id)

    def test_write_side_traversal_protection_c3(self):
        from skills.lifecycle import write_to_draft, update_skill_status, approve_draft_skill, reject_draft_skill
        
        # Test write_to_draft traversal return
        res = write_to_draft(bot_id=1, skill_name="../unsafe_name", content="content")
        self.assertEqual(res, "[非法技能名]")
            
        # Test update_skill_status traversal return
        res = update_skill_status(bot_id=1, skill_name="dir/unsafe", new_status="active")
        self.assertEqual(res, "[非法技能名]")
        
        # Test approve_draft_skill traversal return
        res = approve_draft_skill(bot_id=1, skill_name="unsafe..name")
        self.assertEqual(res, "[非法技能名]")
        
        # Test reject_draft_skill traversal return
        res = reject_draft_skill(bot_id=1, skill_name="unsafe name")
        self.assertEqual(res, "[非法技能名]")

    def test_system_shadow_protection_by_path_a1(self):
        sys_skill_path = self.test_sys / "run-tests.md"
        sys_skill_path.write_text("""---
name: run-tests
always: true
---
System run-tests body""", encoding="utf-8")

        bot_skills_dir = _TEST_WS_ROOT / "bot_ws_1" / "skills"
        personal_skill_path = bot_skills_dir / "run-tests.md"
        personal_skill_path.write_text("""---
name: run-tests
always: true
layer: personal
---
Personal override""", encoding="utf-8")

        skills = _list_skills_all_sync(bot_id=1, group_id=1)
        run_tests_skill = next((s for s in skills if s["name"] == "run-tests"), None)
        
        self.assertIsNotNone(run_tests_skill)
        self.assertEqual(run_tests_skill.get("path"), sys_skill_path)


if __name__ == "__main__":
    unittest.main()

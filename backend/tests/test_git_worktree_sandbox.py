import unittest
import tempfile
import shutil
import os
from pathlib import Path
from unittest.mock import patch

import skills.constants as _const
from workspace import init_group_workspace, write_file, read_file, layout
from workspace.git_worktree import create_worktree, remove_worktree, promote_worktree, use_worktree
from integrations.jira import get_jira


class TestGitWorktreeSandbox(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self._tmp.name).resolve()
        
        # Patch WORKSPACE_ROOT to our temporary directory
        self._patcher = patch("skills.constants.WORKSPACE_ROOT", self.workspace_root)
        self._patcher.start()
        
        self.group_id = 99
        self.task_id = "DFT-123"
        
        # Initialize group workspace
        await init_group_workspace(self.group_id)

    async def asyncTearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    async def test_create_and_remove_worktree(self):
        # 1. Create worktree
        worktree_dir = await create_worktree(self.group_id, self.task_id)
        worktree_workspace = worktree_dir / "workspace"
        
        # Check that directories exist
        self.assertTrue(worktree_dir.exists())
        self.assertTrue(worktree_workspace.exists())
        self.assertTrue((worktree_workspace / ".git").exists())
        
        # Check that shared files/folders are symlinked
        self.assertTrue((worktree_dir / "BOARD.md").is_symlink())
        self.assertTrue((worktree_dir / "docs").is_symlink())
        self.assertTrue((worktree_dir / "skills").is_symlink())
        self.assertTrue((worktree_dir / "prs").is_symlink())

        # 2. Remove worktree
        await remove_worktree(self.group_id, self.task_id)
        self.assertFalse(worktree_dir.exists())

    async def test_dependency_symlinking(self):
        group_dir = layout.group_dir(self.group_id)
        shared_workspace = group_dir / "shared" / "workspace"
        
        # Create mock dependency folders in shared workspace
        node_modules = shared_workspace / "project_a" / "node_modules"
        node_modules.mkdir(parents=True, exist_ok=True)
        (node_modules / "dummy.js").write_text("console.log(1)", encoding="utf-8")
        
        # Create worktree
        worktree_dir = await create_worktree(self.group_id, self.task_id)
        worktree_workspace = worktree_dir / "workspace"
        
        # Check that node_modules is symlinked to the correct location in the worktree workspace
        worktree_node_modules = worktree_workspace / "project_a" / "node_modules"
        self.assertTrue(worktree_node_modules.exists())
        self.assertTrue(worktree_node_modules.is_symlink())
        self.assertEqual(worktree_node_modules.resolve(), node_modules.resolve())
        
        # Clean up worktree
        await remove_worktree(self.group_id, self.task_id)

    async def test_vfs_path_overrides_isolation(self):
        # Create worktree
        worktree_dir = await create_worktree(self.group_id, self.task_id)
        
        # 1. Write file outside worktree (default shared directory)
        res1 = await write_file(bot_id=1, path="workspace/src/foo.py", content="shared content", group_id=self.group_id)
        self.assertIn("已写入", res1)
        
        # 2. Write file inside worktree using use_worktree context manager
        with use_worktree(worktree_dir):
            res2 = await write_file(bot_id=1, path="workspace/src/foo.py", content="sandboxed content", group_id=self.group_id)
            self.assertIn("已写入", res2)
            
            # Read file inside worktree context should yield sandboxed content
            content_inside = await read_file(bot_id=1, path="workspace/src/foo.py", group_id=self.group_id)
            self.assertEqual(content_inside, "sandboxed content")
            
        # 3. Read file outside worktree context should yield shared content
        content_outside = await read_file(bot_id=1, path="workspace/src/foo.py", group_id=self.group_id)
        self.assertEqual(content_outside, "shared content")

        # Clean up worktree
        await remove_worktree(self.group_id, self.task_id)

    async def test_promote_worktree_merges_changes(self):
        # Create worktree
        worktree_dir = await create_worktree(self.group_id, self.task_id)
        
        # Write files inside worktree
        with use_worktree(worktree_dir):
            await write_file(bot_id=1, path="workspace/src/feature.py", content="print('new feature')", group_id=self.group_id)
            
        # Promote changes (merge back to main)
        await promote_worktree(self.group_id, self.task_id, target_branch="main")
        
        # Verify worktree is removed
        self.assertFalse(worktree_dir.exists())
        
        # Verify file now exists in the shared workspace main branch
        shared_workspace_file = layout.group_shared_dir(self.group_id) / "workspace" / "src" / "feature.py"
        self.assertTrue(shared_workspace_file.exists())
        self.assertEqual(shared_workspace_file.read_text(encoding="utf-8"), "print('new feature')")

    async def test_jira_done_trigger_promotion(self):
        # Create a Jira ticket mock
        jira = get_jira()
        ticket = await jira.create_ticket(self.group_id, title="Implement Feature X")
        ticket_id = ticket["ticket_id"]
        
        # Create worktree for this ticket
        worktree_dir = await create_worktree(self.group_id, ticket_id)
        
        # Write file inside worktree
        with use_worktree(worktree_dir):
            await write_file(bot_id=1, path="workspace/src/jira_feature.py", content="jira content", group_id=self.group_id)
            
        # Update Jira ticket to "done" which should trigger promote_worktree automatically
        await jira.update_ticket(self.group_id, ticket_id, status="done")
        
        # Verify worktree is removed and files are merged
        self.assertFalse(worktree_dir.exists())
        shared_file = layout.group_shared_dir(self.group_id) / "workspace" / "src" / "jira_feature.py"
        self.assertTrue(shared_file.exists())
        self.assertEqual(shared_file.read_text(encoding="utf-8"), "jira content")

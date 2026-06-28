import unittest
import tempfile
import shutil
import os
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import skills.constants as _const
import db as _database
import db.writer as _db_writer
from workspace import init_group_workspace, write_file, read_file, layout
from workspace.git_worktree import create_worktree, remove_worktree, promote_worktree, use_worktree, _run_git_cmd
from integrations.jira import get_jira
from core.runner import run_unit
from core.orchestration.base import WorkUnit


class TestGitWorktreeSandbox(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self._tmp.name).resolve()

        # Patch WORKSPACE_ROOT to our temporary directory
        self._patcher = patch("skills.constants.WORKSPACE_ROOT", self.workspace_root)
        self._patcher.start()

        # Pin the DB path globals to an isolated temp DB and build its full schema
        # (init_db runs migrations, incl. the `tickets` table jira.create_ticket
        # needs). Without this, these process-wide globals are left pointing at
        # whichever other suite imported last — and once that suite deletes its
        # test DB in tearDown, our write_connect auto-creates a fresh schema-less
        # DB at the stale path → 'no such table: tickets' (collection-order flake).
        self._db_file = str(self.workspace_root / "test_git_worktree.db")
        self._orig_db_paths = (_database.DB_PATH, _db_writer.DB_PATH)
        _database.DB_PATH = self._db_file
        _db_writer.DB_PATH = self._db_file
        await _database.init_db()

        self.group_id = 99
        self.task_id = "DFT-123"

        # Initialize group workspace
        await init_group_workspace(self.group_id)

    async def asyncTearDown(self):
        self._patcher.stop()
        _database.DB_PATH, _db_writer.DB_PATH = self._orig_db_paths
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
        with use_worktree(self.group_id, worktree_dir):
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
        with use_worktree(self.group_id, worktree_dir):
            await write_file(bot_id=1, path="workspace/src/feature.py", content="print('new feature')", group_id=self.group_id)
            
        # Promote changes (merge back to main)
        await promote_worktree(self.group_id, self.task_id, target_branch="main")
        
        # Verify worktree is removed
        self.assertFalse(worktree_dir.exists())
        
        # Verify file now exists in the shared workspace main branch
        shared_workspace_file = layout.group_shared_dir(self.group_id) / "workspace" / "src" / "feature.py"
        self.assertTrue(shared_workspace_file.exists())
        self.assertEqual(shared_workspace_file.read_text(encoding="utf-8"), "print('new feature')")

    async def test_git_worktree_baseline_preserves_changes(self):
        """🔴 #1 & 🟠 #6: Verify pre-existing uncommitted files in shared workspace are committed as baseline before check out."""
        shared_workspace = layout.group_shared_dir(self.group_id) / "workspace"
        shared_workspace.mkdir(parents=True, exist_ok=True)
        
        # Create an existing project file before git init or create_worktree is called
        pre_existing_file = shared_workspace / "existing_code.py"
        pre_existing_file.write_text("print('pre-existing')", encoding="utf-8")
        
        # Create worktree
        worktree_dir = await create_worktree(self.group_id, self.task_id)
        worktree_workspace = worktree_dir / "workspace"
        
        # Verify that pre-existing file exists in the worktree sandbox workspace
        worktree_file = worktree_workspace / "existing_code.py"
        self.assertTrue(worktree_file.exists())
        self.assertEqual(worktree_file.read_text(encoding="utf-8"), "print('pre-existing')")
        
        # Clean up
        await remove_worktree(self.group_id, self.task_id)

    async def test_promote_worktree_fails_on_conflict_aborts_cleanly(self):
        """🔴 #4: Verify merge conflict aborts cleanly and keeps main untainted."""
        # 1. Create a worktree
        worktree_dir = await create_worktree(self.group_id, self.task_id)
        
        # 2. Write file inside worktree
        with use_worktree(self.group_id, worktree_dir):
            await write_file(bot_id=1, path="workspace/conflict.py", content="content A", group_id=self.group_id)
            
        # 3. Create a conflict by writing the same file differently on main
        await write_file(bot_id=1, path="workspace/conflict.py", content="content B", group_id=self.group_id)
        
        # 4. Promote and assert conflict error is raised
        with self.assertRaises(RuntimeError) as context:
            await promote_worktree(self.group_id, self.task_id, target_branch="main")
        self.assertIn("merge conflict", str(context.exception).lower())
        
        # 5. Check that main workspace git is clean (merge aborted) and no conflicted files remain
        shared_workspace = layout.group_shared_dir(self.group_id) / "workspace"
        status = await _run_git_cmd(shared_workspace, "status", "--porcelain")
        self.assertEqual(status.strip(), "")
        
        # Clean up
        await remove_worktree(self.group_id, self.task_id)

    async def test_self_promotion_mid_run_deferred(self):
        """🔴 #3: Verify self-promotion inside bot run is deferred and does not delete directory mid-run."""
        # Setup Jira mock ticket
        jira = get_jira()
        ticket = await jira.create_ticket(self.group_id, title="Test Ticket")
        ticket_id = ticket["ticket_id"]
        
        # Create worktree
        worktree_dir = await create_worktree(self.group_id, ticket_id)
        
        # Mock executor to simulate update_ticket(status="done") mid-run
        async def mock_run(ctx):
            # Check directory exists
            self.assertTrue(worktree_dir.exists())
            # Perform update ticket status to done mid-run
            await jira.update_ticket(self.group_id, ticket_id, status="done")
            # Verify directory STILL exists (promotion deferred)
            self.assertTrue(worktree_dir.exists())
            
            # Write a dummy file to verify files can still be written safely
            await write_file(bot_id=1, path="workspace/some_code.py", content="some content", group_id=self.group_id)
            
            # Mock return ExecutionResult
            from executors.base import ExecutionResult
            return ExecutionResult(full_text="done", msg_id=None)

        # Wire up a mock unit execution
        mock_executor = MagicMock()
        mock_executor.run = mock_run
        
        # Temporary patch of executors registry
        from executors import registry as exec_registry
        exec_registry._registry["mock_tool_loop"] = mock_executor
        
        unit = WorkUnit(bot={"id": 1, "name": "Dev"}, executor_id="mock_tool_loop", tag={"ticket_id": ticket_id})
        
        try:
            orch = MagicMock()
            orch.start_time.return_value = None
            from core.orchestration.base import OrchestratorStep
            orch.observe.return_value = OrchestratorStep(confirm_gate=None, done=False, announcements=[], next_units=[])
            # Run unit
            await run_unit(self.group_id, unit, orch)
        finally:
            exec_registry._registry.pop("mock_tool_loop", None)
        
        # Verify that promotion was executed AFTER the run completed and directories were removed
        self.assertFalse(worktree_dir.exists())
        
        # Verify files were merged successfully
        shared_file = layout.group_shared_dir(self.group_id) / "workspace" / "some_code.py"
        self.assertTrue(shared_file.exists())
        self.assertEqual(shared_file.read_text(encoding="utf-8"), "some content")

    async def test_silent_failure_surface_error(self):
        """🔴 #2 & 🟡 #9: Verify that promotion errors are surfaced and do not fail silently."""
        jira = get_jira()
        ticket = await jira.create_ticket(self.group_id, title="Failing Ticket")
        ticket_id = ticket["ticket_id"]
        
        # Create worktree
        worktree_dir = await create_worktree(self.group_id, ticket_id)
        
        # 1. Modify worktree file
        with use_worktree(self.group_id, worktree_dir):
            await write_file(bot_id=1, path="workspace/fail.py", content="content X", group_id=self.group_id)
            
        # 2. Modify same file on main to guarantee a conflict
        await write_file(bot_id=1, path="workspace/fail.py", content="content Y", group_id=self.group_id)
        
        # 3. Trigger promotion via jira update_ticket and assert it raises RuntimeError (not swallowed silently)
        with patch("core.runner._post_system_msg") as mock_post:
            with self.assertRaises(RuntimeError):
                await jira.update_ticket(self.group_id, ticket_id, status="done")
            # Verify system warning was posted to chat
            mock_post.assert_called_once()
            self.assertIn("自动合并失败", mock_post.call_args[0][2])
            
        # Clean up
        await remove_worktree(self.group_id, ticket_id)

    async def test_cross_ticket_deferred_promotion_drain(self):
        """Verify that a deferred promotion of ticket Y is successfully drained
        when the run for ticket X completes.
        """
        jira = get_jira()
        
        # 1. Create tickets X and Y
        ticket_x = await jira.create_ticket(self.group_id, title="Ticket X")
        ticket_y = await jira.create_ticket(self.group_id, title="Ticket Y")
        tx_id = ticket_x["ticket_id"]
        ty_id = ticket_y["ticket_id"]
        
        # 2. Create worktrees for X and Y
        wt_x = await create_worktree(self.group_id, tx_id)
        wt_y = await create_worktree(self.group_id, ty_id)
        
        # Write some change in Y's worktree
        with use_worktree(self.group_id, wt_y):
            await write_file(bot_id=1, path="workspace/y_code.py", content="y value", group_id=self.group_id)
            
        # 3. Simulate bot run lock being held for X
        from core import bg
        async with bg.group_run_lock(self.group_id):
            # Under the lock, mark Y done (simulating out-of-band/human update)
            await jira.update_ticket(self.group_id, ty_id, status="done")
            # Verify Y's worktree still exists (promotion deferred)
            self.assertTrue(wt_y.exists())

        # 4. Now run unit for X. When it finishes, its post-execution drain should promote Y.
        async def mock_run(ctx):
            from executors.base import ExecutionResult
            return ExecutionResult(full_text="run x completed", msg_id=None)

        mock_executor = MagicMock()
        mock_executor.run = mock_run
        
        from executors import registry as exec_registry
        exec_registry._registry["mock_tool_loop"] = mock_executor
        unit = WorkUnit(bot={"id": 1, "name": "Dev"}, executor_id="mock_tool_loop", tag={"ticket_id": tx_id})
        
        try:
            orch = MagicMock()
            orch.start_time.return_value = None
            from core.orchestration.base import OrchestratorStep
            orch.observe.return_value = OrchestratorStep(confirm_gate=None, done=False, announcements=[], next_units=[])
            
            await run_unit(self.group_id, unit, orch)
        finally:
            exec_registry._registry.pop("mock_tool_loop", None)
            
        # 5. Verify Y is promoted (its worktree is removed, and its changes are merged to main)
        self.assertFalse(wt_y.exists())
        shared_y_file = layout.group_shared_dir(self.group_id) / "workspace" / "y_code.py"
        self.assertTrue(shared_y_file.exists())
        self.assertEqual(shared_y_file.read_text(encoding="utf-8"), "y value")
        
        # Clean up X's worktree
        await remove_worktree(self.group_id, tx_id)

    async def test_hydration_time_worktree_pruning(self):
        """Verify that hydration-time worktree sweep cleans up stale worktrees and branches,
        and allows successful recreation of the worktrees afterwards.
        """
        jira = get_jira()
        ticket_z = await jira.create_ticket(self.group_id, title="Ticket Z")
        tz_id = ticket_z["ticket_id"]
        
        # 1. Create worktree for Z
        wt_z = await create_worktree(self.group_id, tz_id)
        self.assertTrue(wt_z.exists())
        
        # Write some file
        with use_worktree(self.group_id, wt_z):
            await write_file(bot_id=1, path="workspace/z_code.py", content="z value", group_id=self.group_id)
            
        # 2. Simulate hydration sweep by calling prune_group_worktrees directly
        from workspace.git_worktree import prune_group_worktrees
        await prune_group_worktrees(self.group_id)
        
        # Verify Z's worktree dir was cleaned up
        self.assertFalse(wt_z.exists())
        
        # 3. Verify Z's worktree can be recreated successfully (no git registration conflicts)
        new_wt_z = await create_worktree(self.group_id, tz_id)
        self.assertTrue(new_wt_z.exists())
        
        # Clean up
        await remove_worktree(self.group_id, tz_id)

    async def test_hydration_promotion_and_pruning(self):
        """Verify that group hydration promotes 'done' worktrees and prunes remaining stale ones."""
        jira = get_jira()
        
        # 1. Create tickets X (stale/in_progress) and Y (done but deferred promotion)
        ticket_x = await jira.create_ticket(self.group_id, title="Ticket X")
        ticket_y = await jira.create_ticket(self.group_id, title="Ticket Y")
        tx_id = ticket_x["ticket_id"]
        ty_id = ticket_y["ticket_id"]
        
        # Create worktrees
        wt_x = await create_worktree(self.group_id, tx_id)
        wt_y = await create_worktree(self.group_id, ty_id)
        
        # Write some file inside Y's worktree
        with use_worktree(self.group_id, wt_y):
            await write_file(bot_id=1, path="workspace/y_hydration.py", content="y hydration changes", group_id=self.group_id)
            
        # Write some file inside X's worktree
        with use_worktree(self.group_id, wt_x):
            await write_file(bot_id=1, path="workspace/x_hydration.py", content="x hydration changes", group_id=self.group_id)
            
        # 2. Simulate out-of-band update setting Y to done (deferred because X has active worktree or similar,
        # but here we can just update status in database directly and keep the worktree folder intact)
        await jira.update_ticket(self.group_id, ty_id, status="done")
        
        # 3. Simulate group hydration
        from runtime.lifecycle import LifecycleManager
        lm = LifecycleManager()
        await lm.hydrate(self.group_id)
        
        # 4. Verify Y (done status) is promoted successfully (directory deleted, changes merged)
        self.assertFalse(wt_y.exists())
        shared_y = layout.group_shared_dir(self.group_id) / "workspace" / "y_hydration.py"
        self.assertTrue(shared_y.exists())
        self.assertEqual(shared_y.read_text(encoding="utf-8"), "y hydration changes")
        
        # 5. Verify X (in_progress status) is pruned (directory deleted, changes NOT merged)
        self.assertFalse(wt_x.exists())
        shared_x = layout.group_shared_dir(self.group_id) / "workspace" / "x_hydration.py"
        self.assertFalse(shared_x.exists())

    @patch("core.runner.exec_registry")
    async def test_temporary_chat_worktree_promotion(self, mock_exec_registry):
        class MockResult:
            def __init__(self):
                self.full_text = "success"
                
        # Mock executor to write a file in the worktree
        class MockExecutor:
            async def run(self, ctx):
                # Write a file using workspace write_file
                # Since ctx.active_ticket_id is set to the temp ticket, VFS will write into the worktree
                await write_file(
                    bot_id=1,
                    path="workspace/chat_game.py",
                    content="print('chat game')",
                    group_id=ctx.group_id
                )
                return MockResult()
                
        mock_executor = MockExecutor()
        mock_exec_registry.get.return_value = mock_executor
        
        # Create a work unit without ticket_id in tag
        unit = WorkUnit(
            bot={"id": 1, "name": "dev"},
            trigger_msg="write a game",
            executor_id="mock_exec",
            tag={},
        )
        
        class MockStep:
            def __init__(self):
                self.announcements = []
                self.confirm_gate = None
                self.broadcast_state = False
                self.done = True
                self.next_units = []

        class MockOrch:
            def participant_count(self, group_id):
                return 1
            def observe(self, group_id, bot_id, full_text, signals=None):
                return MockStep()
        mock_orch = MockOrch()
        
        # Run run_unit
        res = await run_unit(self.group_id, unit, mock_orch)
        self.assertIsNone(res)
        
        # Verify the file was promoted back to the shared workspace
        shared_file = layout.group_shared_dir(self.group_id) / "workspace" / "chat_game.py"
        self.assertTrue(shared_file.exists())
        self.assertEqual(shared_file.read_text(encoding="utf-8"), "print('chat game')")
        
        # Verify that no worktree folders are left behind
        worktrees_dir = layout.group_dir(self.group_id) / "worktrees"
        if worktrees_dir.exists():
            worktree_folders = [f for f in worktrees_dir.iterdir() if f.is_dir()]
            self.assertEqual(len(worktree_folders), 0)

    @patch("core.runner.exec_registry")
    async def test_nested_git_repo_promotion(self, mock_exec_registry):
        class MockResult:
            def __init__(self):
                self.full_text = "success"
                
        class MockExecutor:
            async def run(self, ctx):
                # Write a nested file and initialize a nested git directory in the worktree
                await write_file(
                    bot_id=1,
                    path="workspace/nested-project/test.txt",
                    content="hello nested",
                    group_id=ctx.group_id
                )
                # Create a nested .git directory and write some content in it
                # We need to resolve the path within the active worktree (ctx.group_id, temp ticket)
                from workspace.layout import group_dir
                wt_workspace = group_dir(ctx.group_id) / "worktrees" / f"task_{ctx.active_ticket_id}" / "workspace"
                nested_git = wt_workspace / "nested-project" / ".git"
                nested_git.mkdir(parents=True, exist_ok=True)
                (nested_git / "config").write_text("[core]\n\trepositoryformatversion = 0", encoding="utf-8")
                
                return MockResult()
                
        mock_executor = MockExecutor()
        mock_exec_registry.get.return_value = mock_executor
        
        unit = WorkUnit(
            bot={"id": 1, "name": "dev"},
            trigger_msg="create nested git",
            executor_id="mock_exec",
            tag={},
        )
        
        class MockStep:
            def __init__(self):
                self.announcements = []
                self.confirm_gate = None
                self.broadcast_state = False
                self.done = True
                self.next_units = []

        class MockOrch:
            def participant_count(self, group_id):
                return 1
            def observe(self, group_id, bot_id, full_text, signals=None):
                return MockStep()
        mock_orch = MockOrch()
        
        await run_unit(self.group_id, unit, mock_orch)
        
        # Verify the nested file and the nested .git directory were promoted back to the shared workspace
        shared_project_dir = layout.group_shared_dir(self.group_id) / "workspace" / "nested-project"
        self.assertTrue((shared_project_dir / "test.txt").exists())
        
        shared_nested_git = shared_project_dir / ".git"
        self.assertTrue(shared_nested_git.exists())
        self.assertTrue(shared_nested_git.is_dir())
        self.assertTrue((shared_nested_git / "config").exists())
        self.assertEqual((shared_nested_git / "config").read_text(encoding="utf-8"), "[core]\n\trepositoryformatversion = 0")

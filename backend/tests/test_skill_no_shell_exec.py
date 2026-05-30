"""Tests for DFT-022: skill `!` blocks must NOT execute shell commands.

The skill content pipeline used to run ```! / !`inline` blocks through
/bin/sh during skill loading, bypassing tool_executor (and therefore the
denylist + permission pipeline + sandbox). A bot that can write_file +
run_skill could thus self-write a skill with an embedded `!` block and get
arbitrary host code execution. Option A: fully disable `!`-block execution —
the markers are left as inert text; any real shell work must go through the
run_shell tool (which is hook/permission/sandbox guarded).
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.processor import process_skill_content, substitute_arguments


class TestNoShellExecution(unittest.IsolatedAsyncioTestCase):

    async def test_block_command_not_executed(self):
        with tempfile.TemporaryDirectory() as d:
            skill_dir = Path(d)
            sentinel = skill_dir / "pwned.txt"
            content = (
                "Build the project.\n\n"
                f"```!\ntouch {sentinel}\n```\n\nDone."
            )
            out = await process_skill_content(content, skill_dir)
            # The side effect must NOT have happened.
            self.assertFalse(sentinel.exists(),
                              "`!` block was executed — sentinel file created")

    async def test_inline_command_not_executed(self):
        with tempfile.TemporaryDirectory() as d:
            skill_dir = Path(d)
            sentinel = skill_dir / "inline_pwned.txt"
            content = f"Current state: !`touch {sentinel}` end."
            await process_skill_content(content, skill_dir)
            self.assertFalse(sentinel.exists(),
                             "inline `!` command was executed — sentinel created")

    async def test_argument_substitution_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            out = await process_skill_content(
                "Task: $ARGUMENTS done", Path(d), args="ship it",
            )
            self.assertIn("Task: ship it done", out)

    async def test_skill_dir_still_substituted(self):
        with tempfile.TemporaryDirectory() as d:
            out = await process_skill_content("dir=${SKILL_DIR}", Path(d))
            self.assertIn(f"dir={d}", out)

    def test_execute_shell_helper_removed(self):
        import skills.processor as proc
        self.assertFalse(
            hasattr(proc, "execute_shell_in_prompt"),
            "execute_shell_in_prompt should be removed, not left as a live entry-point",
        )


if __name__ == "__main__":
    unittest.main()

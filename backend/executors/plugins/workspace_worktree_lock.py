"""Per-event-loop, per-group worktree serialization."""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager


class WorktreeLocks:
    def __init__(self, workspace):
        self.workspace = workspace
        self._locks: dict = {}
        self._guard = threading.Lock()

    def get(self, group_id: int):
        loop_id = id(asyncio.get_running_loop())
        with self._guard:
            per_loop = self._locks.setdefault(loop_id, {})
            return per_loop.setdefault(group_id, asyncio.Lock())

    def for_dir(self, work_dir, group_id):
        if group_id is None:
            return None
        root = self.workspace.group_workspace(group_id).resolve() / "workspace"
        return self.get(group_id) if work_dir.resolve().is_relative_to(root) else None

    @asynccontextmanager
    async def maybe(self, lock):
        if lock is None:
            yield
        else:
            async with lock:
                yield

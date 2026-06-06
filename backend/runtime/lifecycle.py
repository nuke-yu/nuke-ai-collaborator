"""CELL-17: Group lifecycle management (hydration and eviction)."""
import asyncio
import logging
import os
import time
from collections import OrderedDict

import db
from runtime.dbpaths import group_db_path

log = logging.getLogger(__name__)

import sys

class GroupLock:
    def __init__(self, group_id: int):
        self.fd = None
        from skills.constants import WORKSPACE_ROOT
        from pathlib import Path
        self.lock_file = Path(WORKSPACE_ROOT) / f"group_{group_id}" / "group.lock"

    def acquire(self) -> bool:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = open(self.lock_file, "w")
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd.write(str(os.getpid()))
            self.fd.flush()
            return True
        except Exception as e:
            log.warning("Failed to acquire group lock for %s: %s", self.lock_file, e)
            if self.fd:
                try: self.fd.close()
                except Exception: pass
                self.fd = None
            return False

    def release(self):
        if self.fd:
            try:
                if sys.platform != "win32":
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self.fd.close()
            except Exception:
                pass
            self.fd = None
            try:
                self.lock_file.unlink(missing_ok=True)
            except Exception:
                pass

    def __del__(self):
        self.release()


class LifecycleManager:
    def __init__(self, max_groups: int = 100):
        self.max_groups = max_groups
        # group_id -> last_active_timestamp
        self._active_groups: OrderedDict[int, float] = OrderedDict()
        self._lock = asyncio.Lock()
        self._locks: dict[int, GroupLock] = {}

    def is_active(self, group_id: int) -> bool:
        """Is this group currently hydrated/owned by this worker? Public predicate
        so callers don't reach into the private _active_groups map."""
        return group_id in self._active_groups

    async def hydrate(self, group_id: int) -> str:
        """Ensure a group is ready for work. Returns the DB path."""
        path = group_db_path(group_id)
        
        async with self._lock:
            if group_id in self._active_groups:
                self._active_groups.move_to_end(group_id)
                self._active_groups[group_id] = time.time()
                return path

            # New hydration
            log.info("lifecycle: hydrating group %d", group_id)
            
            # Acquire group file lock to prevent split-brain double worker executions
            glock = GroupLock(group_id)
            if not glock.acquire():
                raise RuntimeError(f"Failed to acquire lease lock for group {group_id}. Another worker process is likely holding it.")
            self._locks[group_id] = glock
            
            # Ensure workspace directory exists
            os.makedirs(os.path.dirname(path), exist_ok=True)
            
            # 1. Initialize schema (CELL-05 logic)
            from db.schema_split import init_group_db
            await init_group_db(path)
            
            # 2. Run any pending migrations (CELL-10.1)
            async with db.connect(path) as conn:
                from db.migrations import run_migrations
                await run_migrations(conn)
            
            
            # Steps 3 & 4 read/write GROUP-private tables (tickets / workflow_state /
            # agent_sessions). Bind the group DB so their connect()/get_db() resolve
            # to it — otherwise they hit the central DB, which has no such tables.
            with db.bind_db(path):
                # 3. RDManager pre-scan
                try:
                    from core.orchestration.rd_manager import rd_manager
                    await rd_manager.check_board(group_id)
                except Exception:
                    pass

                # 4. Resume workflows and recover sessions (CELL-22)
                try:
                    from core import runner
                    import sessions
                    await runner.resume_workflows(group_id=group_id)
                    await sessions.recover_all(group_id=group_id)
                except Exception:
                    log.exception("lifecycle: failed to recover group %d", group_id)


            # Evict if over limit
            if len(self._active_groups) >= self.max_groups:
                await self._evict_lru()
                
            self._active_groups[group_id] = time.time()
            return path


    async def evict(self, group_id: int) -> None:
        """Explicitly evict a group (used for CELL-18 lease release)."""
        async with self._lock:
            if group_id in self._active_groups:
                del self._active_groups[group_id]
                await self._do_evict(group_id)

    async def _do_evict(self, gid: int) -> None:
        log.info("lifecycle: evicting group %d", gid)
        
        # 1. Abort any running tasks
        from core import bg
        bg.abort_group(gid)
        
        # 2. Clear memory states
        try:
            from core.orchestration.rd_manager import rd_manager
            if hasattr(rd_manager, "_last_tickets"):
                rd_manager._last_tickets.pop(gid, None)
        except Exception:
            pass

        try:
            from permissions import engine as perm_engine
            if hasattr(perm_engine, "cancel_pending_for_group"):
                perm_engine.cancel_pending_for_group(gid)
        except Exception:
            pass
            
        # 3. Clear this group's VFS path locks so they don't accumulate in a
        # long-lived worker (M-5). Group tasks were aborted in step 1, so no
        # in-flight file op should still hold one.
        try:
            from workspace import clear_group_locks
            clear_group_locks(gid)
        except Exception:
            pass

        # 4. Close DB writer
        await db.aclose_writer(group_db_path(gid))

        # 5. Release file lock
        glock = self._locks.pop(gid, None)
        if glock:
            glock.release()

    async def _evict_lru(self) -> None:
        if not self._active_groups:
            return
        gid, _ = self._active_groups.popitem(last=False)
        await self._do_evict(gid)


    async def shutdown(self) -> None:
        """Close all active groups."""
        async with self._lock:
            for gid in list(self._active_groups):
                await db.aclose_writer(group_db_path(gid))
                glock = self._locks.pop(gid, None)
                if glock:
                    glock.release()
            self._active_groups.clear()

    def stats(self) -> dict:
        return {
            "active_groups_count": len(self._active_groups),
            "active_groups": list(self._active_groups.keys()),
        }

# Global manager instance
manager = LifecycleManager()

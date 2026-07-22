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
        from workspace import layout
        self.lock_file = layout.group_dir(group_id) / "group.lock"

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
                try:
                    self.fd.close()
                except Exception:
                    log.exception("Failed to close group lock fd after acquire failure for %s", self.lock_file)
                self.fd = None
            return False

    def release(self):
        if self.fd:
            try:
                if sys.platform != "win32":
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
            except Exception:
                log.exception("Failed to unlock group lock for %s", self.lock_file)
            try:
                self.fd.close()
            except Exception:
                log.exception("Failed to close group lock fd for %s", self.lock_file)
            self.fd = None
            try:
                self.lock_file.unlink(missing_ok=True)
            except Exception:
                log.exception("Failed to remove group lock file %s", self.lock_file)

    def __del__(self):
        # Interpreter shutdown can tear down logging / import state before
        # destructors run. Skip best-effort cleanup in that phase to avoid noisy
        # "sys.meta_path is None" errors during process exit.
        if getattr(sys, "is_finalizing", None) and sys.is_finalizing():
            return
        self.release()


class LifecycleManager:
    def __init__(self, max_groups: int = 100):
        self.max_groups = max_groups
        # group_id -> last_active_timestamp
        self._active_groups: OrderedDict[int, float] = OrderedDict()
        self._hydrating: dict[int, asyncio.Future] = {}
        self._evicting: dict[int, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._locks: dict[int, GroupLock] = {}
        self._evictor_task: asyncio.Task | None = None
        self._shutting_down = False

    def is_active(self, group_id: int) -> bool:
        """Is this group currently hydrated/owned by this worker? Public predicate
        so callers don't reach into the private _active_groups map."""
        return group_id in self._active_groups

    def touch(self, group_id: int) -> None:
        """Update last active timestamp for a group."""
        if group_id in self._active_groups:
            self._active_groups.move_to_end(group_id)
            self._active_groups[group_id] = time.time()

    def _ensure_evictor_task_locked(self) -> None:
        if self._evictor_task is None or self._evictor_task.done():
            self._evictor_task = asyncio.create_task(self._background_loop())

    async def _background_loop(self) -> None:
        log.info("lifecycle: starting background eviction and pruning loop")
        last_prune = 0.0
        while True:
            try:
                await asyncio.sleep(60)
                await self.sweep_inactive_groups()
                await self._drain_projection_outboxes()
                
                # Run prune once a day (86400 seconds)
                now = time.time()
                if now - last_prune > 86400:
                    await self.prune_resources()
                    last_prune = now
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("lifecycle: error in background loop")

    async def _drain_projection_outboxes(self) -> None:
        """Deliver durable memory projections for groups owned by this worker."""
        from memory.bootstrap import get_memory_module

        async with self._lock:
            group_ids = tuple(self._active_groups)
        memory_module = get_memory_module()
        for group_id in group_ids:
            try:
                await memory_module.drain_groups((group_id,))
            except Exception:
                log.exception(
                    "lifecycle: failed to drain projection outbox for group %d",
                    group_id,
                )

    def _group_has_active_tasks(self, gid: int) -> bool:
        try:
            from core import bg
            group_tasks = bg._group_tasks.get(gid, set())
        except Exception:
            return False
        return any(not t.done() for t in group_tasks)

    def _pick_lru_evictable_group(self) -> int | None:
        for gid in self._active_groups:
            if not self._group_has_active_tasks(gid):
                return gid
        return None

    async def sweep_inactive_groups(self) -> None:
        """Find groups inactive for GROUP_INACTIVITY_TIMEOUT (default 30 mins) and evict them."""
        timeout = float(os.environ.get("GROUP_INACTIVITY_TIMEOUT", 1800))
        now = time.time()
            
        candidates = []
        async with self._lock:
            for gid, last_active in list(self._active_groups.items()):
                if now - last_active > timeout and not self._group_has_active_tasks(gid):
                    candidates.append((gid, last_active))
        for gid, last_active in candidates:
            await self._evict_if_still_inactive(gid, last_active, now, timeout)

    async def _evict_if_still_inactive(
        self, gid: int, expected_last_active: float, now: float, timeout: float
    ) -> bool:
        should_evict = False
        async with self._lock:
            current_last_active = self._active_groups.get(gid)
            if current_last_active is None:
                return False
            if current_last_active != expected_last_active:
                return False
            if now - current_last_active <= timeout:
                return False
            if self._group_has_active_tasks(gid):
                return False
            self._active_groups.pop(gid, None)
            should_evict = True
        if should_evict:
            await self._evict_once(gid)
            return True
        return False

    async def prune_resources(self) -> None:
        """Prune logs and temporary workspace files older than 14 days."""
        from pathlib import Path
        log.info("lifecycle: running resource pruning")
        now = time.time()
        fourteen_days_seconds = 14 * 24 * 3600
        
        # 1. Prune logs (logs/worker-*.log)
        try:
            log_dir = Path("logs")
            if log_dir.exists():
                for log_file in log_dir.glob("worker-*.log"):
                    if log_file.is_file():
                        mtime = log_file.stat().st_mtime
                        if now - mtime > fourteen_days_seconds:
                            log.info("lifecycle: pruning old log file %s", log_file)
                            log_file.unlink(missing_ok=True)
        except Exception:
            log.exception("lifecycle: error pruning logs")
            
        # 2. Prune temporary workspace files (workspaces/temp/*)
        try:
            from skills.constants import WORKSPACE_ROOT
            temp_dir = Path(WORKSPACE_ROOT) / "temp"
            if temp_dir.exists():
                for root, dirs, files in os.walk(temp_dir, topdown=False):
                    for name in files:
                        filepath = Path(root) / name
                        if filepath.is_file():
                            mtime = filepath.stat().st_mtime
                            if now - mtime > fourteen_days_seconds:
                                log.info("lifecycle: pruning old temp workspace file %s", filepath)
                                filepath.unlink(missing_ok=True)
                    for name in dirs:
                        dirpath = Path(root) / name
                        if dirpath.is_dir() and not os.listdir(dirpath):
                            dirpath.rmdir()
        except Exception:
            log.exception("lifecycle: error pruning temp workspaces")

    async def hydrate(self, group_id: int) -> str:
        """Ensure a group is ready for work. Returns the DB path."""
        path = group_db_path(group_id)

        while True:
            fut = None
            evicting = None
            async with self._lock:
                if self._shutting_down:
                    raise RuntimeError("lifecycle manager is shutting down")
                self._ensure_evictor_task_locked()
                if group_id in self._active_groups:
                    self._active_groups.move_to_end(group_id)
                    self._active_groups[group_id] = time.time()
                    return path
                evicting = self._evicting.get(group_id)
                if evicting is None:
                    fut = self._hydrating.get(group_id)
                    if fut is None:
                        fut = asyncio.get_running_loop().create_future()
                        self._hydrating[group_id] = fut
                        leader = True
                    else:
                        leader = False
                    break
            await asyncio.shield(evicting)

        if not leader:
            return await asyncio.shield(fut)

        glock = None
        try:
            # New hydration
            log.info("lifecycle: hydrating group %d", group_id)

            # Acquire group file lock to prevent split-brain double worker executions
            glock = GroupLock(group_id)
            if not glock.acquire():
                raise RuntimeError(f"Failed to acquire lease lock for group {group_id}. Another worker process is likely holding it.")

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
                # Rebuild durable projection intents from canonical SQLite state,
                # then deliver them. This repairs the commit→Chroma crash window
                # and external vector-index loss without blocking DB migration.
                try:
                    from memory.bootstrap import get_memory_module
                    await get_memory_module().reconcile_group(group_id)
                except Exception:
                    log.exception(
                        "lifecycle: failed to reconcile memory projections for group %d",
                        group_id,
                    )

                # Drain deferred promotions and prune stale worktrees on hydration
                try:
                    from workspace import layout
                    worktrees_dir = layout.group_dir(group_id) / "worktrees"
                    if worktrees_dir.exists():
                        from integrations.jira import get_jira
                        from workspace.git_worktree import promote_worktree, prune_group_worktrees
                        tickets = await get_jira().list_tickets(group_id)
                        status_by_id = {t["ticket_id"]: t["status"] for t in tickets}

                        for item in list(worktrees_dir.iterdir()):
                            if item.is_dir() and item.name.startswith("task_"):
                                tid = item.name[5:]
                                if status_by_id.get(tid) == "done":
                                    log.info(f"Hydration drain: promoting deferred task {tid} in group {group_id}")
                                    try:
                                        await promote_worktree(group_id, tid)
                                    except Exception as pe:
                                        log.exception(f"Failed to execute deferred promotion for task {tid} on hydration: {pe}")

                        # Prune remaining stale worktrees
                        await prune_group_worktrees(group_id)
                except Exception:
                    log.exception("lifecycle: failed to drain promotions/prune worktrees for group %d on hydration", group_id)

                # 3. RDManager pre-scan
                try:
                    from core.orchestration.rd_manager import rd_manager
                    await rd_manager.check_board(group_id)
                except Exception:
                    log.exception("lifecycle: failed to run RDManager pre-scan for group %d", group_id)

                # 4. Resume workflows and recover sessions (CELL-22)
                try:
                    from core import runner
                    import sessions
                    await runner.resume_workflows(group_id=group_id)
                    await sessions.recover_all(group_id=group_id)
                except Exception:
                    log.exception("lifecycle: failed to recover group %d", group_id)

            to_evict = None
            async with self._lock:
                if self._shutting_down:
                    fut = self._hydrating.pop(group_id, None)
                    err = RuntimeError("lifecycle manager is shutting down")
                    if fut and not fut.done():
                        fut.set_exception(err)
                    raise err
                self._locks[group_id] = glock
                glock = None
                if len(self._active_groups) >= self.max_groups:
                    to_evict = self._pick_lru_evictable_group()
                    if to_evict is not None:
                        self._active_groups.pop(to_evict, None)
                self._active_groups[group_id] = time.time()
                fut = self._hydrating.pop(group_id, None)
                if fut and not fut.done():
                    fut.set_result(path)
            if to_evict is not None:
                await self._evict_once(to_evict)
            return path
        except Exception as exc:
            async with self._lock:
                fut = self._hydrating.pop(group_id, None)
                if fut and not fut.done():
                    fut.set_exception(exc)
            raise
        finally:
            if glock is not None:
                glock.release()


    async def evict(self, group_id: int) -> None:
        """Explicitly evict a group (used for CELL-18 lease release)."""
        should_evict = False
        inflight = None
        evicting = None
        async with self._lock:
            if group_id in self._active_groups:
                del self._active_groups[group_id]
                should_evict = True
            else:
                inflight = self._hydrating.get(group_id)
                evicting = self._evicting.get(group_id)
        if inflight is not None:
            try:
                await asyncio.shield(inflight)
            except Exception:
                return
            async with self._lock:
                if group_id in self._active_groups:
                    del self._active_groups[group_id]
                    should_evict = True
                else:
                    evicting = self._evicting.get(group_id)
        if evicting is not None:
            await asyncio.shield(evicting)
            return
        if should_evict:
            await self._evict_once(group_id)

    async def _evict_once(self, gid: int) -> None:
        fut = None
        async with self._lock:
            fut = self._evicting.get(gid)
            if fut is None:
                fut = asyncio.get_running_loop().create_future()
                self._evicting[gid] = fut
                leader = True
            else:
                leader = False

        if not leader:
            await asyncio.shield(fut)
            return

        try:
            await self._do_evict(gid)
        except Exception as exc:
            async with self._lock:
                fut = self._evicting.pop(gid, None)
                if fut and not fut.done():
                    fut.set_exception(exc)
            raise
        else:
            async with self._lock:
                fut = self._evicting.pop(gid, None)
                if fut and not fut.done():
                    fut.set_result(None)

    async def _do_evict(self, gid: int) -> None:
        log.info("lifecycle: evicting group %d", gid)
        
        # R5 Handoff Barrier: Force serialize and save orchestrator state before aborting tasks
        try:
            from core.orchestration import registry as orch_registry
            import core.workflow as wf
            from core import workflow_store
            
            orch_id = wf._group_orch.get(gid)
            if orch_id:
                orch = orch_registry.get(orch_id)
                blob = orch.serialize(gid)
                if blob is not None:
                    log.info("lifecycle: force saving orchestrator state for group %d before eviction", gid)
                    await workflow_store.save_state(gid, orch_id, blob)
        except Exception:
            log.exception("lifecycle: failed to force save state for group %d during eviction", gid)

        # 1. Abort any running tasks
        from core import bg
        try:
            bg.abort_group(gid)
        except Exception:
            log.exception("lifecycle: failed to abort group %d during eviction", gid)
        
        # 2. Clear memory states
        try:
            from core.orchestration.rd_manager import rd_manager
            if hasattr(rd_manager, "_last_tickets"):
                rd_manager._last_tickets.pop(gid, None)
        except Exception:
            log.exception("lifecycle: failed to clear rd-manager cache for group %d", gid)

        try:
            from permissions import engine as perm_engine
        except Exception:
            perm_engine = None
        if perm_engine is not None:
            try:
                if hasattr(perm_engine, "cancel_pending_for_group"):
                    perm_engine.cancel_pending_for_group(gid)
            except Exception:
                log.exception("lifecycle: failed to cancel pending permissions for group %d", gid)
            try:
                if hasattr(perm_engine, "clear_once_grants_for_group"):
                    perm_engine.clear_once_grants_for_group(gid)
            except Exception:
                log.exception("lifecycle: failed to clear once grants for group %d", gid)
            
        # 3. Clear this group's VFS path locks so they don't accumulate in a
        # long-lived worker (M-5). Group tasks were aborted in step 1, so no
        # in-flight file op should still hold one.
        try:
            from workspace import clear_group_locks
            clear_group_locks(gid)
        except Exception:
            log.exception("lifecycle: failed to clear workspace locks for group %d", gid)

        # 4. Close DB writer, but never strand the group file lock if that fails.
        glock = self._locks.pop(gid, None)
        try:
            await db.aclose_writer(group_db_path(gid))
        finally:
            # 5. Release file lock
            if glock:
                glock.release()

    async def _evict_lru(self) -> None:
        gid = None
        async with self._lock:
            gid = self._pick_lru_evictable_group()
            if gid is not None:
                self._active_groups.pop(gid, None)
        if gid is not None:
            await self._evict_once(gid)


    async def shutdown(self) -> None:
        """Close all active groups."""
        evictor = None
        inflight = []
        evicting = []
        async with self._lock:
            self._shutting_down = True
            if self._evictor_task:
                self._evictor_task.cancel()
                evictor = self._evictor_task
                self._evictor_task = None
            inflight = list(self._hydrating.values())
            evicting = list(self._evicting.values())
            active = list(self._active_groups)
            self._active_groups.clear()
        if evictor is not None:
            await asyncio.gather(evictor, return_exceptions=True)
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        if evicting:
            await asyncio.gather(*evicting, return_exceptions=True)
        for gid in active:
            await self._evict_once(gid)

    def stats(self) -> dict:
        return {
            "active_groups_count": len(self._active_groups),
            "active_groups": list(self._active_groups.keys()),
        }

# Global manager instance
manager = LifecycleManager()

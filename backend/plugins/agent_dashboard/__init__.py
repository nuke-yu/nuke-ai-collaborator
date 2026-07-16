"""
plugins/agent_dashboard/ — Coding Agent Dashboard Plugin

A non-invasive plugin that adds autonomous coding agent capabilities
with a real-time dashboard for monitoring progress.

Capabilities added:
  - POST /api/agent/tasks      — Create coding agent tasks
  - GET  /api/agent/tasks      — List active tasks with progress
  - GET  /api/agent/tasks/{id} — Get task detail + progress
  - POST /api/agent/tasks/{id}/retry — Retry stuck/failed tasks
  - DELETE /api/agent/tasks/{id}    — Abort tasks
  - GET  /api/agent/workers    — Worker load monitoring
  - WS   /ws/agent/{group_id}  — Real-time progress for a task
  - WS   /ws/agent/all         — Real-time progress for all tasks

Architecture:
  - ProgressAdapter subscribes to Supervisor broadcast events (via PluginHost observer)
  - Translates granular events into structured progress: {phase, percent, detail}
  - Dashboard WebSocket clients receive real-time updates
  - StuckDetector runs in background, flags hung tasks for retry

This plugin does NOT modify any existing code. It only:
  1. Observes existing events (read-only)
  2. Mounts its own REST/WS routes (additive)
  3. Runs its own background tasks (isolated)

Removal: Delete this directory. No other changes needed.
"""
import asyncio
import logging

log = logging.getLogger(__name__)


async def register(host):
    """Plugin entry point called by PluginHost during discovery.

    Args:
        host: PluginHost instance providing infrastructure access
    """
    from .progress import ProgressAdapter
    from .stuck_detector import StuckDetector
    from .orchestrator import TaskOrchestrator
    from .task_store import TaskStateProjector, TaskStore
    from .reconciler import TaskReconciler
    from . import websocket as ws_module
    from . import api as api_module

    log.info("agent_dashboard: registering plugin...")

    # Note: CodingAgentOrchestrator is registered via core/orchestration/plugins/coding_agent.py
    # so that Worker processes discover it via registry.discover(). Registering it here
    # (Supervisor process only) would make it unavailable to Workers.

    # 1. Create shared components
    task_store = TaskStore()
    await task_store.reconcile_transient_states()
    projector = TaskStateProjector(task_store)
    adapter = ProgressAdapter(projector=projector)
    adapter.hydrate(await task_store.list_tasks(limit=1000))
    orchestrator = TaskOrchestrator(adapter=adapter, task_store=task_store)
    detector = StuckDetector(adapter, orchestrator=orchestrator)
    reconciler = TaskReconciler(task_store)

    # 2. Inject dependencies into sub-modules
    ws_module.set_adapter(adapter)
    api_module.set_context(adapter, host, detector, orchestrator=orchestrator)

    # 3. Mount REST API routes
    # Write operations (create/retry/abort) require operator-level auth.
    # Read operations (list/get/workers) require user-level auth.
    from core import auth
    from api.admin_deps import require_operator
    from fastapi import Depends
    host.mount_router(api_module.router, prefix="/api/agent",
                      dependencies=[Depends(auth.get_current_user)])

    # 4. Mount Dashboard WebSocket routes (JWT validated inside endpoint)
    host.mount_router(ws_module.router)

    # 5. Subscribe to Supervisor broadcast events
    host.observe_events("agent_dashboard", adapter.on_event)

    # 6. Start background tasks
    host.start_background(projector.run())
    host.start_background(reconciler.run())
    host.start_background(detector.run())
    host.start_background(ws_module.consumer_loop(adapter))

    # Note: Periodic worktree cleanup removed (R2-2). Runner creates chat_<uuid>
    # worktrees that would be incorrectly flagged as orphans. Worktree cleanup
    # is handled by the existing prune_group_worktrees() on group hydration.

    log.info("agent_dashboard: plugin registered successfully")
    log.info("  REST: /api/agent/tasks, /api/agent/workers")
    log.info("  WS:   /ws/agent/{group_id}, /ws/agent/all")

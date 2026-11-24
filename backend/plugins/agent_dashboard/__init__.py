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

The plugin mounts the Supervisor HTTP/WS endpoints and wires the online_tasks
service to the progress observer. Workers discover the coding workflow through
the orchestration registry.
"""
import logging
import os

log = logging.getLogger(__name__)


async def register(host):
    """Plugin entry point called by PluginHost during discovery.

    Args:
        host: PluginHost instance providing infrastructure access
    """
    from online_tasks.progress import ProgressAdapter
    from online_tasks.stuck_detector import StuckDetector
    from online_tasks.service import TaskOrchestrator
    from online_tasks.store import TaskStateProjector, TaskStore
    from online_tasks.reconciler import TaskReconciler
    from . import websocket as ws_module
    from . import api as api_module

    log.info("agent_dashboard: registering plugin...")

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
    from core import auth
    from fastapi import Depends
    host.mount_router(api_module.router, prefix="/api/agent",
                      dependencies=[Depends(auth.get_current_user)])

    from .task_queue_api import router_for
    from online_tasks.story_input import task_scope
    from online_tasks.task_queue_store import TaskQueueStore
    from online_tasks.task_queue import TaskQueueService
    from integrations.github_client import find_task_pull_request

    task_queue = None
    try:
        story_parallel = int(os.getenv(
            "NUKE_ONLINE_STORY_MAX_PARALLEL",
            os.getenv("NUKE_JIRA_MAX_PARALLEL", "2"),
        ))
        if not 1 <= story_parallel <= 32:
            raise ValueError("NUKE_ONLINE_STORY_MAX_PARALLEL must be between 1 and 32")
        github_owner = os.getenv("NUKE_GITHUB_OWNER", "nuke-yu")
        task_queue = TaskQueueService(
            task_scope(), TaskQueueStore(), orchestrator, task_store,
            lambda: host.pick_worker("least_loaded"), find_task_pull_request,
            github_owner=github_owner, max_parallel=story_parallel,
        )
    except ValueError as exc:
        log.warning("Online task queue configuration invalid: %s", exc)
    host.mount_router(router_for(task_queue), prefix="/api/agent")
    if task_queue is not None:
        host.start_background(task_queue.run())

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

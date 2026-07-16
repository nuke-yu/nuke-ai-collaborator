"""CELL-14: thin launcher for the cell topology.

    python -m runtime.entry --role supervisor [--addr ...]
    python -m runtime.entry --role worker --id w0 [--addr ...]

Each role boots its engine; the Worker is wired to the real dispatch
(runtime.dispatch). The FastAPI/WebSocket termination shell and APScheduler on the
Supervisor side are layered on at integration (CELL-13/the WS shell); this module
is the role entrypoint + factory.
"""
import argparse
import asyncio
import logging

from runtime import ipc
import scheduler


def build_worker(worker_id: str, addr: str):
    """Construct a Worker wired to the real dispatch. Side-effect-free: plugin
    discovery is a process-startup concern (run_worker), not construction —
    calling registry.discover() here would re-exec plugin modules and pollute the
    global tool_executor for any later test."""
    from runtime.worker import Worker
    from runtime.dispatch import dispatch_user_message
    return Worker(worker_id, addr, dispatch=dispatch_user_message)


def build_supervisor(addr: str, **kwargs):
    from runtime.supervisor import Supervisor
    return Supervisor(addr, **kwargs)


def _init_tool_router() -> None:
    """Register the worker's ToolRouter providers (synchronous, no I/O).

    MCP no longer runs in the worker: the cross-group mcp-collector process owns
    all MCP connections, and McpProxyProvider forwards calls to it over the bus
    (schemas arrive via MCP_SCHEMAS pushes). So the worker only needs the proxy +
    the Builtin catch-all — no npx, no per-worker MCP subprocesses.

    Dispatch policy (see tool_loop_v1._dispatch_tool):
      Builtin / skill / shell tools stay on tool_executor.execute() so the global
      before-hooks (permission check + run_shell danger guard) fire. Only MCP
      tools (NOT in tool_executor's registry) route through the proxy.
    """
    import logging
    from executors.tool_router import router as tool_router
    from executors.providers import BuiltinToolProvider
    from executors.providers.mcp_proxy import McpProxyProvider

    log = logging.getLogger(__name__)
    tool_router.register_provider(McpProxyProvider())     # MCP via collector over the bus
    tool_router.register_provider(BuiltinToolProvider())  # catch-all; excluded from external schemas
    log.info("ToolRouter ready: %s", [p.provider_id for p in tool_router._providers])



async def run_worker(worker_id: str, addr: str) -> None:
    from executors import registry
    registry.discover()                       # load bot executor plugins (once, at startup)

    # Register ToolRouter providers (proxy + builtin). Synchronous + no I/O now
    # that MCP lives in the collector, so it's safe to do before connecting.
    _init_tool_router()

    # P0-4: Install GitHub client if NUKE_GITHUB_ENABLED=true.
    # This must happen in each Worker process because the GitClient singleton
    # is per-process and doesn't cross the Supervisor→Worker boundary.
    # Fail closed: if NUKE_GITHUB_ENABLED=true but gh or GITHUB_TOKEN is missing,
    # the Worker startup fails (no silent fallback to LocalGitClient).
    from integrations.github_client import (
        github_integration_enabled,
        install_github_client,
        require_github_integration,
    )
    if github_integration_enabled():
        require_github_integration()
        install_github_client()
        logging.getLogger(__name__).info("GitHub client installed and ready")

    from skills.watcher import watcher
    watcher.start(asyncio.get_running_loop())
    try:
        await build_worker(worker_id, addr).run()
    finally:
        watcher.stop()
        from executors.tool_router import router as tool_router
        await tool_router.close_all()          # terminate MCP subprocesses on worker exit


async def run_supervisor(addr: str, num_workers: int = 8) -> None:
    sup = build_supervisor(addr, num_workers=num_workers)
    await sup.start()
    try:
        await scheduler.start()
        try:
            # TODO(CELL-13/WS shell): wire the FastAPI/WebSocket termination shell
            # into this wait loop when that integration lands.
            await asyncio.Event().wait()   # run until cancelled or externally stopped
        finally:
            scheduler.stop()
    finally:
        await sup.stop()


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(prog="runtime.entry")
    p.add_argument("--role", required=True, choices=["supervisor", "worker", "mcp-collector"])
    p.add_argument("--id", default="w0", help="worker id (worker role)")
    p.add_argument("--addr", default=None, help="IPC address (default per platform)")
    p.add_argument("--workers", type=int, default=8, help="number of worker processes to spawn (supervisor role)")
    args = p.parse_args(argv)

    # supervisor & collector share the supervisor's IPC address (the collector
    # connects to it); workers use their own id for the default address name.
    default_name = "supervisor" if args.role in ("supervisor", "mcp-collector") else args.id
    addr = args.addr or ipc.make_addr(default_name)

    if args.role == "supervisor":
        asyncio.run(run_supervisor(addr, num_workers=args.workers))
    elif args.role == "mcp-collector":
        from runtime.mcp_collector import run_collector
        asyncio.run(run_collector(addr))
    else:
        asyncio.run(run_worker(args.id, addr))


if __name__ == "__main__":
    main()

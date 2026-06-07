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


async def _init_tool_router() -> None:
    """Initialize ToolRouter providers in the background after worker startup.

    Runs as an asyncio task so it does NOT block the worker from connecting
    to the Supervisor.  The _execute_tool_call fallback (tool_executor) handles
    any tool calls that arrive before this completes.

    Priority order: Skill → Shell → MCP (slow: npx subprocess) → Builtin (catch-all)
    """
    import logging
    from pathlib import Path
    from executors.tool_router import router as tool_router
    from executors.providers import BuiltinToolProvider, SkillToolProvider, ShellToolProvider
    from executors.providers.mcp_client import McpClientToolProvider

    log = logging.getLogger(__name__)
    tool_router.register_provider(SkillToolProvider())
    tool_router.register_provider(ShellToolProvider())

    _mcp_cfg = Path(__file__).parent.parent / "mcp_servers.json"
    for _mcp_prov in McpClientToolProvider.from_config(_mcp_cfg):
        try:
            await _mcp_prov.initialize()
            tool_router.register_provider(_mcp_prov)
            log.info(f"MCP provider '{_mcp_prov._server_name}' registered.")
        except Exception as _e:
            log.warning(f"MCP server init failed [{_mcp_prov._server_name}], skipping: {_e}")

    tool_router.register_provider(BuiltinToolProvider())  # catch-all last
    log.info("ToolRouter ready: %s", [p.provider_id for p in tool_router._providers])


async def run_worker(worker_id: str, addr: str) -> None:
    from executors import registry
    registry.discover()                       # load bot executor plugins (once, at startup)

    # Initialize ToolRouter in the background — must NOT block here because the
    # worker needs to connect to the Supervisor first (fast path).  Any tool calls
    # that arrive before the router is ready fall back to tool_executor directly.
    asyncio.create_task(_init_tool_router(), name=f"tool-router-init-{worker_id}")

    from skills.watcher import watcher
    watcher.start(asyncio.get_event_loop())
    try:
        await build_worker(worker_id, addr).run()
    finally:
        watcher.stop()
        from executors.tool_router import router as tool_router
        await tool_router.close_all()          # terminate MCP subprocesses on worker exit


async def run_supervisor(addr: str, num_workers: int = 0) -> None:
    sup = build_supervisor(addr, num_workers=num_workers)
    await sup.start()
    # TODO(CELL-13/WS shell): start APScheduler + FastAPI WS termination here.
    await asyncio.Event().wait()   # run until cancelled


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(prog="runtime.entry")
    p.add_argument("--role", required=True, choices=["supervisor", "worker"])
    p.add_argument("--id", default="w0", help="worker id (worker role)")
    p.add_argument("--addr", default=None, help="IPC address (default per platform)")
    p.add_argument("--workers", type=int, default=0, help="number of worker processes to spawn (supervisor role)")
    args = p.parse_args(argv)

    default_name = "supervisor" if args.role == "supervisor" else args.id
    addr = args.addr or ipc.make_addr(default_name)

    if args.role == "supervisor":
        asyncio.run(run_supervisor(addr, num_workers=args.workers))
    else:
        asyncio.run(run_worker(args.id, addr))


if __name__ == "__main__":
    main()

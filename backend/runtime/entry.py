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


async def run_worker(worker_id: str, addr: str) -> None:
    from executors import registry
    registry.discover()                       # load bot executor plugins (once, at startup)
    from skills.watcher import watcher
    watcher.start(asyncio.get_event_loop())
    try:
        await build_worker(worker_id, addr).run()
    finally:
        watcher.stop()


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

"""core/bg.py — 后台任务登记处（DFT-025 / DFT-027）

asyncio 的事件循环只持有 task 的弱引用：`create_task(...)` 后若不保存返回值，
task 可能在跑完之前就被 GC 掉，异常也只在 GC 时以 warning 形式丢出。本模块统一：
  - spawn():       held + 异常落日志（fire-and-forget 副作用用）
  - spawn_group(): 在 spawn 之上再按 group_id 登记，使 abort 能取消整条工作流链
  - abort_group(): 取消某个群下所有在跑的 task

只依赖标准库，不 import 任何项目模块，避免与 main / runner / orchestrator 形成环。
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

# 持有所有后台 task 的强引用，防止被 GC 提前回收（DFT-025）
_bg_tasks: set[asyncio.Task] = set()

# group_id -> 该群下可被 abort 取消的 task 集合（DFT-027）
_group_tasks: dict[int, set[asyncio.Task]] = {}


def _log_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("background task failed: %r", exc, exc_info=exc)


def _make_group_done(group_id: int):
    def _done(task: asyncio.Task) -> None:
        bucket = _group_tasks.get(group_id)
        if bucket is not None:
            bucket.discard(task)
            if not bucket:
                _group_tasks.pop(group_id, None)
    return _done


def spawn(coro) -> asyncio.Task:
    """Fire-and-forget：持有引用 + 异常落日志（DFT-025）。"""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    task.add_done_callback(_log_exception)
    return task


def spawn_group(group_id: int, coro) -> asyncio.Task:
    """同 spawn()，并把 task 登记到 group，使 abort 可取消（DFT-027）。"""
    task = spawn(coro)
    _group_tasks.setdefault(group_id, set()).add(task)
    task.add_done_callback(_make_group_done(group_id))
    return task


def register_group(group_id: int, task: asyncio.Task) -> None:
    """把一个外部已创建的 task 登记到 group（如 main.py 的 dispatch task）。"""
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    _group_tasks.setdefault(group_id, set()).add(task)
    task.add_done_callback(_make_group_done(group_id))


def abort_group(group_id: int) -> int:
    """取消某个群下所有仍在跑的 task，返回取消的数量。"""
    bucket = _group_tasks.get(group_id)
    if not bucket:
        return 0
    n = 0
    for task in list(bucket):
        if not task.done():
            task.cancel()
            n += 1
    return n

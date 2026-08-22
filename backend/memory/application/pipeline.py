"""Application orchestration for canonical Memory pipeline jobs."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping

from memory.application.context import require_pipeline_repository
from memory.contracts import LostLeaseError
from memory.domain import MemoryScope
from memory.ports import PipelineJobRepositoryPort

log = logging.getLogger(__name__)

PipelineHandler = Callable[[int, str, str], Awaitable[Mapping[str, object]]]


class RetryablePipelineJob(Exception):
    """Signal that a transient condition should return the job to the queue."""


class CanonicalPipelineDispatcher:
    """Execute jobs using only the injected repository port and handlers."""

    def __init__(
        self,
        repository: PipelineJobRepositoryPort | None = None,
        handlers: Mapping[str, PipelineHandler] = (),
    ) -> None:
        self.repository = repository or require_pipeline_repository()
        self.handlers = dict(handlers)

    async def dispatch_group(
        self, group_id: int, *, limit: int = 10, lease_seconds: int = 60,
    ) -> dict[str, int]:
        scope = MemoryScope.group(group_id=group_id, actor_id="service:canonical_pipeline")
        jobs = await self.repository.list_ready(scope, limit=limit)
        processed = failed = 0
        for job in jobs:
            if str(job["job_type"]) not in self.handlers:
                continue
            job_id = str(job["job_id"])
            token = await self.repository.claim(scope, job_id, lease_seconds)
            if not token:
                continue
            try:
                checkpoint_scope = MemoryScope.group(
                    group_id=group_id, actor_id="service:canonical_pipeline_checkpoint"
                )
                previous = await self.repository.latest_checkpoint(checkpoint_scope, job_id)
                claimed = await self.repository.checkpoint(
                    checkpoint_scope, job_id, "claimed", {
                        "job_id": job_id, "job_type": str(job["job_type"]),
                        "input_id": str(job["input_id"]),
                        "input_version": str(job["input_version"]),
                        "attempt": int(job["attempt"]) + 1, "status": "running",
                    }, previous.get("checkpoint_id") if previous else None,
                )
                handler = self.handlers[str(job["job_type"])]
                handler_task = asyncio.create_task(
                    handler(group_id, str(job["input_id"]), str(job["input_version"]))
                )
                heartbeat_lost = asyncio.Event()

                async def _heartbeat() -> None:
                    while True:
                        await asyncio.sleep(max(0.1, lease_seconds / 3))
                        try:
                            renewed = await self.repository.renew_lease(
                                scope, job_id, token, lease_seconds
                            )
                        except Exception:
                            renewed = False
                        if not renewed:
                            heartbeat_lost.set()
                            handler_task.cancel()
                            return

                heartbeat_task = asyncio.create_task(_heartbeat())
                try:
                    output = dict(await handler_task)
                except asyncio.CancelledError:
                    if heartbeat_lost.is_set():
                        raise LostLeaseError(f"Worker lost lease for job {job_id}")
                    raise
                finally:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
                if heartbeat_lost.is_set():
                    raise LostLeaseError(f"Worker lost lease for job {job_id}")
                if not await self.repository.complete_with_checkpoint(
                    scope, job_id, token, json.dumps(output, ensure_ascii=False),
                    thread_id=job_id, state={
                        "job_id": job_id, "job_type": str(job["job_type"]),
                        "input_id": str(job["input_id"]), "status": "completed",
                        "output": output,
                    }, parent_checkpoint_id=claimed["checkpoint_id"],
                ):
                    raise LostLeaseError(f"Worker lost lease for job {job_id}")
                processed += 1
            except Exception as exc:
                if isinstance(exc, RetryablePipelineJob):
                    await self.repository.defer(scope, job_id, token)
                    continue
                failed += 1
                log.exception("canonical memory pipeline job failed: %s", job_id)
                await self.repository.fail(scope, job_id, token, str(exc))
        return {"claimed": processed + failed, "completed": processed, "failed": failed}


__all__ = [
    "CanonicalPipelineDispatcher",
    "PipelineHandler", "RetryablePipelineJob",
]

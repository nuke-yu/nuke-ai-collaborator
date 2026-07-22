"""Projection adapters backed by the application's existing AI implementations."""
from __future__ import annotations

from typing import Any, Mapping


class LegacyExperienceProjectionDelivery:
    async def deliver(
        self, projection_type: str, payload: Mapping[str, Any]
    ) -> None:
        if projection_type != "experience_vector_upsert":
            raise ValueError(f"unsupported memory projection type: {projection_type}")
        from ai.experiences import _index_vector

        await _index_vector(
            str(payload["record_id"]),
            str(payload["content"]),
            int(payload["group_id"]),
            int(payload["bot_id"]) if payload.get("bot_id") is not None else None,
            float(payload["confidence"]),
        )


class LegacyExperienceProjectionReconciler:
    async def reconcile(self, group_id: int) -> int:
        from ai.experiences import reconcile_experience_projections

        return await reconcile_experience_projections(group_id)


def redact_projection_error(message: str) -> str:
    """Use the host's secret redactor without coupling the outbox engine to it."""
    from executors.redaction import redact_secrets

    redacted, _ = redact_secrets(message)
    return redacted

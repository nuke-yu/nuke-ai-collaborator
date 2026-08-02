"""Central enforcement for redacted, summarized, and reference-only payloads."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from executors.redaction import redact_secrets

from .event_policy import (
    OBSERVABILITY_KEY,
    EventPolicy,
    PayloadPolicy,
    classify_event,
    enrich_event_payload,
)


ARTIFACT_KEY = "_artifact"
SUMMARY_KEY = "_summary"
SUMMARY_INLINE_BYTES = 4_096
REDACTED_INLINE_BYTES = 16_384
SUMMARY_PREVIEW_CHARS = 2_000

_CORRELATION_KEYS = frozenset({
    "arguments_sha256", "bot_id", "decision_source", "force_ask", "from_status",
    "gate_id", "gate_instance_id", "group_id", "model", "permission_id",
    "persistence", "session_id", "spawn_depth", "stage_id", "status",
    "tool_call_id", "tool_name", "trace_id", "workflow_id",
})
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)


class PayloadArtifactError(RuntimeError):
    """An artifact required to reconstruct a recovery payload is unavailable."""


@dataclass(frozen=True)
class PayloadArtifact:
    artifact_id: str
    event_id: str
    payload_policy: str
    content_sha256: str
    byte_size: int
    content_json: str

    def reference(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "media_type": "application/json",
        }


@dataclass(frozen=True)
class PreparedPayload:
    payload: dict[str, Any]
    artifact: PayloadArtifact | None = None


def _redact_value(value: Any, *, key_hint: str = "") -> Any:
    if isinstance(value, str):
        if len(value) >= 6 and _SENSITIVE_KEY.search(key_hint):
            return "[REDACTED]"
        return redact_secrets(value)[0]
    if isinstance(value, Mapping):
        return {
            str(key): _redact_value(item, key_hint=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, key_hint=key_hint) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_secrets(str(value))[0]


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _summary_projection(payload: Mapping[str, Any], canonical: str) -> dict[str, Any]:
    projected = {
        key: value
        for key, value in payload.items()
        if key in _CORRELATION_KEYS and isinstance(value, (str, int, float, bool, type(None)))
    }
    observability = payload.get(OBSERVABILITY_KEY)
    if isinstance(observability, Mapping):
        projected[OBSERVABILITY_KEY] = dict(observability)
    if len(canonical) <= SUMMARY_PREVIEW_CHARS:
        preview = canonical
    else:
        head = int(SUMMARY_PREVIEW_CHARS * 0.7)
        tail = SUMMARY_PREVIEW_CHARS - head
        preview = f"{canonical[:head]}…<elided {len(canonical) - head - tail} chars>…{canonical[-tail:]}"
    projected[SUMMARY_KEY] = preview
    return projected


def prepare_payload(
    event_type: str,
    payload: Mapping[str, Any] | None,
    *,
    trace_id: str | None = None,
    policy: EventPolicy | None = None,
) -> PreparedPayload:
    """Apply policy deterministically before an observation crosses storage/API boundaries."""
    resolved = policy or classify_event(event_type, payload)
    enriched = enrich_event_payload(event_type, payload, trace_id=trace_id)
    if policy is not None:
        metadata = resolved.to_metadata()
        if trace_id:
            metadata["trace_id"] = trace_id
        enriched[OBSERVABILITY_KEY] = metadata
    sanitized = _redact_value(enriched)
    canonical = _canonical_json(sanitized)
    byte_size = len(canonical.encode("utf-8"))
    threshold = {
        PayloadPolicy.SUMMARY: SUMMARY_INLINE_BYTES,
        PayloadPolicy.REDACTED: REDACTED_INLINE_BYTES,
        PayloadPolicy.REFERENCE_ONLY: 0,
    }[resolved.payload_policy]

    # Automatically record event to OpenTelemetry & Prometheus exporters
    try:
        from .otel_exporter import get_otel_exporter
        from .prometheus_exporter import get_prometheus_metrics

        get_otel_exporter().record_event_policy(event_type, sanitized, resolved, trace_id=trace_id)
        status_str = "error" if "error" in sanitized else "success"
        get_prometheus_metrics().record_event_policy(event_type, resolved, status=status_str)
    except Exception:
        pass

    if byte_size <= threshold:
        return PreparedPayload(payload=sanitized)

    metadata = sanitized.get(OBSERVABILITY_KEY) or {}
    event_id = str(metadata.get("event_id") or f"evt_{uuid.uuid4().hex}")
    artifact = PayloadArtifact(
        artifact_id=f"art_{uuid.uuid4().hex}",
        event_id=event_id,
        payload_policy=resolved.payload_policy.value,
        content_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        byte_size=byte_size,
        content_json=canonical,
    )
    projected = _summary_projection(sanitized, canonical)
    projected[ARTIFACT_KEY] = artifact.reference()
    return PreparedPayload(payload=projected, artifact=artifact)


async def persist_artifact(conn, group_id: int, artifact: PayloadArtifact | None) -> None:
    if artifact is None:
        return
    await conn.execute(
        """INSERT OR IGNORE INTO observation_artifacts
           (artifact_id,group_id,event_id,payload_policy,content_sha256,byte_size,content_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            artifact.artifact_id,
            group_id,
            artifact.event_id,
            artifact.payload_policy,
            artifact.content_sha256,
            artifact.byte_size,
            artifact.content_json,
        ),
    )


async def hydrate_payload(conn, group_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    reference = payload.get(ARTIFACT_KEY)
    if not isinstance(reference, Mapping) or not reference.get("artifact_id"):
        return dict(payload)
    artifact_id = str(reference["artifact_id"])
    async with conn.execute(
        """SELECT content_json,content_sha256 FROM observation_artifacts
           WHERE artifact_id = ? AND group_id = ?""",
        (artifact_id, group_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise PayloadArtifactError(f"Payload artifact unavailable: {artifact_id}")
    content_json, stored_sha256 = row
    actual_sha256 = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
    expected_sha256 = str(reference.get("sha256") or stored_sha256)
    if actual_sha256 != stored_sha256 or actual_sha256 != expected_sha256:
        raise PayloadArtifactError(f"Payload artifact integrity check failed: {artifact_id}")
    value = json.loads(content_json)
    if not isinstance(value, dict):
        raise PayloadArtifactError(f"Payload artifact is not an object: {artifact_id}")
    return value


async def get_artifact(conn, group_id: int, artifact_id: str) -> dict[str, Any] | None:
    async with conn.execute(
        """SELECT artifact_id,event_id,payload_policy,content_sha256,byte_size,
                  content_json,created_at
           FROM observation_artifacts WHERE artifact_id = ? AND group_id = ?""",
        (artifact_id, group_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    content_json = row[5]
    if hashlib.sha256(content_json.encode("utf-8")).hexdigest() != row[3]:
        raise PayloadArtifactError(f"Payload artifact integrity check failed: {artifact_id}")
    return {
        "artifact_id": row[0],
        "event_id": row[1],
        "payload_policy": row[2],
        "sha256": row[3],
        "byte_size": row[4],
        "payload": json.loads(content_json),
        "created_at": row[6],
    }

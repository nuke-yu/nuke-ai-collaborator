"""Unified Artifact Model Manager for multi-bot deliverables, uploads, tool outputs, and workflow assets.

Provides deterministic artifact registering, locator resolution, authorization scoping,
and cross-bot file exchange abstractions with strict Group Isolation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping

import db as _db

log = logging.getLogger(__name__)


class ArtifactOrigin(StrEnum):
    UPLOAD = "upload"
    TOOL = "tool"
    WORKSPACE = "workspace"
    WORKFLOW = "workflow"
    CONNECTOR = "connector"


class ArtifactScope(StrEnum):
    GROUP = "group"
    BOT_ONLY = "bot_only"
    PRIVATE = "private"


class ArtifactLifecycle(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    DELETED = "deleted"


class ArtifactNotFoundError(ValueError):
    """Artifact with requested ID was not found or belongs to another group."""


class ArtifactAccessDeniedError(PermissionError):
    """Access to requested artifact is denied under current authorization scope."""


class ArtifactLifecycleError(ValueError):
    """Artifact lifecycle or derivation transition is invalid."""


@dataclass
class Artifact:
    artifact_id: str
    group_id: int
    origin: str
    mime_type: str
    display_name: str
    storage_locator: str
    checksum_sha256: str
    size_bytes: int
    session_id: str | None = None
    bot_id: int | None = None
    workflow_run_id: str | None = None
    authorization_scope: str = ArtifactScope.GROUP.value
    metadata: dict[str, Any] = field(default_factory=dict)
    artifact_version: int = 1
    parent_artifact_id: str | None = None
    derives_from: str | None = None
    created_by: str = ""
    deleted_at: str | None = None
    lifecycle_status: str = ArtifactLifecycle.ACTIVE.value
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "group_id": self.group_id,
            "session_id": self.session_id,
            "bot_id": self.bot_id,
            "workflow_run_id": self.workflow_run_id,
            "origin": self.origin,
            "mime_type": self.mime_type,
            "display_name": self.display_name,
            "storage_locator": self.storage_locator,
            "checksum_sha256": self.checksum_sha256,
            "size_bytes": self.size_bytes,
            "authorization_scope": self.authorization_scope,
            "metadata": self.metadata,
            "artifact_version": self.artifact_version,
            "parent_artifact_id": self.parent_artifact_id,
            "derives_from": self.derives_from,
            "created_by": self.created_by,
            "deleted_at": self.deleted_at,
            "lifecycle_status": self.lifecycle_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def generate_artifact_id() -> str:
    """Generate a unique 36-character artifact ID prefixed with art_."""
    return f"art_{uuid.uuid4().hex}"


def calculate_checksum(data_bytes: bytes) -> str:
    """Calculate SHA256 hex digest of file data bytes."""
    return hashlib.sha256(data_bytes).hexdigest()


async def register_artifact(
    group_id: int,
    display_name: str,
    origin: str | ArtifactOrigin,
    storage_locator: str,
    mime_type: str = "application/octet-stream",
    size_bytes: int = 0,
    checksum_sha256: str | None = None,
    session_id: str | None = None,
    bot_id: int | None = None,
    workflow_run_id: str | None = None,
    authorization_scope: str | ArtifactScope = ArtifactScope.GROUP,
    metadata: Mapping[str, Any] | None = None,
    artifact_id: str | None = None,
    artifact_version: int = 1,
    parent_artifact_id: str | None = None,
    derives_from: str | None = None,
    created_by: str = "",
) -> Artifact:
    """Register a new Artifact in the group DB."""
    art_id = artifact_id or generate_artifact_id()
    origin_val = origin.value if isinstance(origin, ArtifactOrigin) else str(origin)
    scope_val = authorization_scope.value if isinstance(authorization_scope, ArtifactScope) else str(authorization_scope)
    meta_json = json.dumps(dict(metadata or {}), ensure_ascii=False)
    chksum = checksum_sha256 or ""

    async with _db.connect() as conn:
        for relation_name, related_id in (
            ("parent_artifact_id", parent_artifact_id),
            ("derives_from", derives_from),
        ):
            if not related_id:
                continue
            if related_id == art_id:
                raise ArtifactLifecycleError(f"{relation_name} cannot reference the artifact itself")
            async with conn.execute(
                "SELECT group_id FROM group_artifacts WHERE artifact_id = ?",
                (related_id,),
            ) as relation_cursor:
                relation_row = await relation_cursor.fetchone()
            if relation_row is None or int(relation_row[0]) != int(group_id):
                raise ArtifactLifecycleError(
                    f"{relation_name} must reference an artifact in the same group: {related_id}"
                )

        await conn.execute(
            """INSERT INTO group_artifacts (
                artifact_id, group_id, session_id, bot_id, workflow_run_id,
                origin, mime_type, display_name, storage_locator, checksum_sha256,
                size_bytes, authorization_scope, metadata_json, artifact_version,
                parent_artifact_id, derives_from, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                art_id, group_id, session_id, bot_id, workflow_run_id,
                origin_val, mime_type, display_name, storage_locator, chksum,
                size_bytes, scope_val, meta_json, artifact_version,
                parent_artifact_id, derives_from, created_by,
            ),
        )
        await conn.commit()

    return await get_artifact(art_id, group_id=group_id)


async def get_artifact(artifact_id: str, group_id: int | None = None) -> Artifact:
    """Retrieve Artifact by ID with optional Group Isolation enforcement."""
    async with _db.connect() as conn:
        if group_id is not None:
            query = "SELECT * FROM group_artifacts WHERE artifact_id = ? AND group_id = ? AND lifecycle_status != 'deleted'"
            params = (artifact_id, group_id)
        else:
            query = "SELECT * FROM group_artifacts WHERE artifact_id = ? AND lifecycle_status != 'deleted'"
            params = (artifact_id,)

        async with conn.execute(query, params) as cursor:
            row = await cursor.fetchone()

        if row is None:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id} (group={group_id})")

        # Map sqlite3.Row or tuple
        keys = [column[0] for column in cursor.description]
        data = dict(zip(keys, row))

        metadata = {}
        if data.get("metadata_json"):
            try:
                metadata = json.loads(data["metadata_json"])
            except Exception:
                metadata = {}

        return Artifact(
            artifact_id=data["artifact_id"],
            group_id=data["group_id"],
            session_id=data.get("session_id"),
            bot_id=data.get("bot_id"),
            workflow_run_id=data.get("workflow_run_id"),
            origin=data["origin"],
            mime_type=data["mime_type"],
            display_name=data["display_name"],
            storage_locator=data["storage_locator"],
            checksum_sha256=data.get("checksum_sha256", ""),
            size_bytes=int(data.get("size_bytes") or 0),
            authorization_scope=data.get("authorization_scope", "group"),
            metadata=metadata,
            artifact_version=int(data.get("artifact_version") or 1),
            parent_artifact_id=data.get("parent_artifact_id"),
            derives_from=data.get("derives_from"),
            created_by=data.get("created_by") or "",
            deleted_at=data.get("deleted_at"),
            lifecycle_status=data.get("lifecycle_status") or ArtifactLifecycle.ACTIVE.value,
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


async def list_artifacts(
    group_id: int,
    origin: str | None = None,
    session_id: str | None = None,
    bot_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Artifact]:
    """List Artifacts for a group with optional filtering."""
    conditions = ["group_id = ?", "lifecycle_status != 'deleted'"]
    params: list[Any] = [group_id]

    if origin:
        conditions.append("origin = ?")
        params.append(origin)
    if session_id:
        conditions.append("session_id = ?")
        params.append(session_id)
    if bot_id:
        conditions.append("bot_id = ?")
        params.append(bot_id)

    where_clause = " WHERE " + " AND ".join(conditions)
    query = f"SELECT * FROM group_artifacts{where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    results = []
    async with _db.connect() as conn:
        async with conn.execute(query, tuple(params)) as cursor:
            keys = [column[0] for column in cursor.description]
            async for row in cursor:
                data = dict(zip(keys, row))
                metadata = {}
                if data.get("metadata_json"):
                    try:
                        metadata = json.loads(data["metadata_json"])
                    except Exception:
                        pass
                results.append(
                    Artifact(
                        artifact_id=data["artifact_id"],
                        group_id=data["group_id"],
                        session_id=data.get("session_id"),
                        bot_id=data.get("bot_id"),
                        workflow_run_id=data.get("workflow_run_id"),
                        origin=data["origin"],
                        mime_type=data["mime_type"],
                        display_name=data["display_name"],
                        storage_locator=data["storage_locator"],
                        checksum_sha256=data.get("checksum_sha256", ""),
                        size_bytes=int(data.get("size_bytes") or 0),
                        authorization_scope=data.get("authorization_scope", "group"),
                        metadata=metadata,
                        artifact_version=int(data.get("artifact_version") or 1),
                        parent_artifact_id=data.get("parent_artifact_id"),
                        derives_from=data.get("derives_from"),
                        created_by=data.get("created_by") or "",
                        deleted_at=data.get("deleted_at"),
                        lifecycle_status=data.get("lifecycle_status") or ArtifactLifecycle.ACTIVE.value,
                        created_at=str(data.get("created_at") or ""),
                        updated_at=str(data.get("updated_at") or ""),
                    )
                )

    return results


async def delete_artifact(artifact_id: str, group_id: int) -> bool:
    """Soft-delete the Artifact record; physical storage is retained for audit."""
    async with _db.connect() as conn:
        cursor = await conn.execute(
            "UPDATE group_artifacts SET lifecycle_status = 'deleted', deleted_at = datetime('now'), updated_at = datetime('now') WHERE artifact_id = ? AND group_id = ? AND lifecycle_status != 'deleted'",
            (artifact_id, group_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


def _safe_artifact_path(group_id: int, locator: str) -> Path | None:
    """Resolve a locator only when it is inside the owning Group workspace."""
    if not locator or locator.startswith(("http://", "https://", "s3://")):
        return None
    from workspace import layout
    root = layout.group_dir(group_id).resolve()
    try:
        candidate = Path(locator).expanduser().resolve(strict=False)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    if candidate == root or candidate.is_dir():
        return None
    return candidate


async def purge_deleted_artifacts(
    group_id: int, *, older_than_seconds: int = 86400, dry_run: bool = False
) -> dict[str, Any]:
    """Physically remove expired deleted artifacts, then remove DB tombstones.

    Only Group-local files are eligible. External locators remain auditable and
    are reported as skipped; a DB row is never removed before physical cleanup.
    """
    if older_than_seconds < 0:
        raise ValueError("older_than_seconds must be non-negative")
    async with _db.connect() as conn:
        async with conn.execute(
            """SELECT artifact_id,storage_locator FROM group_artifacts
               WHERE group_id=? AND lifecycle_status='deleted'
                 AND deleted_at <= datetime('now', ?)""",
            (group_id, f"-{int(older_than_seconds)} seconds"),
        ) as cur:
            rows = await cur.fetchall()
        purged: list[str] = []
        skipped: list[dict[str, str]] = []
        for artifact_id, locator in rows:
            path = _safe_artifact_path(group_id, str(locator or ""))
            if path is None:
                skipped.append({"artifact_id": str(artifact_id), "reason": "external_or_unsafe_locator"})
                continue
            if not dry_run and path.exists():
                try:
                    path.unlink()
                except OSError as exc:
                    skipped.append({"artifact_id": str(artifact_id), "reason": f"unlink_failed:{exc}"})
                    continue
            if not dry_run:
                await conn.execute(
                    "DELETE FROM group_artifacts WHERE artifact_id=? AND group_id=? AND lifecycle_status='deleted'",
                    (artifact_id, group_id),
                )
            purged.append(str(artifact_id))
        if not dry_run:
            await conn.commit()
    return {"group_id": group_id, "purged": purged, "skipped": skipped, "dry_run": dry_run}


async def revoke_artifact(artifact_id: str, group_id: int) -> bool:
    """Revoke an artifact without destroying its audit record or storage locator."""
    async with _db.connect() as conn:
        cursor = await conn.execute(
            """UPDATE group_artifacts
               SET lifecycle_status = 'revoked', updated_at = datetime('now')
               WHERE artifact_id = ? AND group_id = ? AND lifecycle_status = 'active'""",
            (artifact_id, group_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def get_artifact_lineage(artifact_id: str, group_id: int) -> dict[str, list[dict[str, Any]] | str]:
    """Return same-group ancestors and descendants for audit and workflow handoff."""
    async with _db.connect() as conn:
        async with conn.execute(
            "SELECT artifact_id, parent_artifact_id, derives_from FROM group_artifacts WHERE artifact_id = ? AND group_id = ?",
            (artifact_id, group_id),
        ) as cursor:
            current = await cursor.fetchone()
        if current is None:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id} (group={group_id})")

        ancestors: list[dict[str, Any]] = []
        visited = {artifact_id}
        parent_id = current[1] or current[2]
        while parent_id and parent_id not in visited:
            visited.add(parent_id)
            async with conn.execute(
                "SELECT * FROM group_artifacts WHERE artifact_id = ? AND group_id = ?",
                (parent_id, group_id),
            ) as parent_cursor:
                parent_row = await parent_cursor.fetchone()
                parent_keys = [column[0] for column in parent_cursor.description]
            if parent_row is None:
                break
            parent_data = dict(zip(parent_keys, parent_row))
            ancestors.append({
                "artifact_id": parent_data["artifact_id"],
                "artifact_version": int(parent_data.get("artifact_version") or 1),
                "lifecycle_status": parent_data.get("lifecycle_status") or ArtifactLifecycle.ACTIVE.value,
                "display_name": parent_data.get("display_name") or "",
            })
            parent_id = parent_data.get("parent_artifact_id") or parent_data.get("derives_from")

        descendants: list[dict[str, Any]] = []
        async with conn.execute(
            """SELECT artifact_id, artifact_version, lifecycle_status, display_name
               FROM group_artifacts
               WHERE group_id = ? AND (parent_artifact_id = ? OR derives_from = ?)
               ORDER BY artifact_version ASC, created_at ASC""",
            (group_id, artifact_id, artifact_id),
        ) as cursor:
            async for row in cursor:
                descendants.append({
                    "artifact_id": row[0],
                    "artifact_version": int(row[1] or 1),
                    "lifecycle_status": row[2] or ArtifactLifecycle.ACTIVE.value,
                    "display_name": row[3] or "",
                })

    return {"artifact_id": artifact_id, "ancestors": ancestors, "descendants": descendants}

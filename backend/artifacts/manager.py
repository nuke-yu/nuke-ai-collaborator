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


class ArtifactNotFoundError(ValueError):
    """Artifact with requested ID was not found or belongs to another group."""


class ArtifactAccessDeniedError(PermissionError):
    """Access to requested artifact is denied under current authorization scope."""


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
) -> Artifact:
    """Register a new Artifact in the group DB."""
    art_id = artifact_id or generate_artifact_id()
    origin_val = origin.value if isinstance(origin, ArtifactOrigin) else str(origin)
    scope_val = authorization_scope.value if isinstance(authorization_scope, ArtifactScope) else str(authorization_scope)
    meta_json = json.dumps(dict(metadata or {}), ensure_ascii=False)
    chksum = checksum_sha256 or ""

    async with _db.connect() as conn:
        await conn.execute(
            """INSERT INTO group_artifacts (
                artifact_id, group_id, session_id, bot_id, workflow_run_id,
                origin, mime_type, display_name, storage_locator, checksum_sha256,
                size_bytes, authorization_scope, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                art_id, group_id, session_id, bot_id, workflow_run_id,
                origin_val, mime_type, display_name, storage_locator, chksum,
                size_bytes, scope_val, meta_json,
            ),
        )
        await conn.commit()

    return await get_artifact(art_id, group_id=group_id)


async def get_artifact(artifact_id: str, group_id: int | None = None) -> Artifact:
    """Retrieve Artifact by ID with optional Group Isolation enforcement."""
    async with _db.connect() as conn:
        if group_id is not None:
            query = "SELECT * FROM group_artifacts WHERE artifact_id = ? AND group_id = ?"
            params = (artifact_id, group_id)
        else:
            query = "SELECT * FROM group_artifacts WHERE artifact_id = ?"
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
    conditions = ["group_id = ?"]
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
                        created_at=str(data.get("created_at") or ""),
                        updated_at=str(data.get("updated_at") or ""),
                    )
                )

    return results


async def delete_artifact(artifact_id: str, group_id: int) -> bool:
    """Delete Artifact record from DB."""
    async with _db.connect() as conn:
        cursor = await conn.execute(
            "DELETE FROM group_artifacts WHERE artifact_id = ? AND group_id = ?",
            (artifact_id, group_id),
        )
        await conn.commit()
        return cursor.rowcount > 0

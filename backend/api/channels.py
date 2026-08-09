"""Operator control plane for Channel delivery operations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.admin_deps import audit_control_plane, require_operator
from channels.stores import ChannelStore
from runtime.dbpaths import channel_bridge_db_path


router = APIRouter(prefix="/api/channels", tags=["channels"])


def _store() -> ChannelStore:
    return ChannelStore(channel_bridge_db_path())


@router.get("/health")
async def channel_health(request: Request, user=Depends(require_operator)):
    store = _store()
    await store.initialize()
    result = await store.get_delivery_health()
    from runtime import supervisor as supervisor_module
    supervisor = supervisor_module.supervisor
    delivery = getattr(supervisor, "_channel_delivery", None) if supervisor else None
    result["dispatcher"] = delivery.snapshot() if delivery is not None else {"up": False}
    audit_control_plane("channels.health", user, request)
    return result


@router.post("/{channel_instance_id}/pause")
async def pause_channel(channel_instance_id: str, request: Request, user=Depends(require_operator)):
    store = _store()
    await store.initialize()
    await store.set_channel_paused(channel_instance_id, True)
    audit_control_plane("channels.pause", user, request, channel_instance_id=channel_instance_id)
    return {"channel_instance_id": channel_instance_id.lower(), "paused": True}


@router.post("/{channel_instance_id}/resume")
async def resume_channel(channel_instance_id: str, request: Request, user=Depends(require_operator)):
    store = _store()
    await store.initialize()
    await store.set_channel_paused(channel_instance_id, False)
    audit_control_plane("channels.resume", user, request, channel_instance_id=channel_instance_id)
    return {"channel_instance_id": channel_instance_id.lower(), "paused": False}


@router.post("/replay")
async def replay_channel_delivery(request: Request, user=Depends(require_operator)):
    body = await request.json()
    key = str(body.get("idempotency_key") or "").strip() if isinstance(body, dict) else ""
    if not key:
        from fastapi import HTTPException
        raise HTTPException(400, "idempotency_key is required")
    store = _store()
    await store.initialize()
    replayed = await store.replay_dead_letter(key)
    audit_control_plane("channels.replay", user, request, idempotency_key=key, replayed=replayed)
    return {"idempotency_key": key, "replayed": replayed}

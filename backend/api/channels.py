"""Operator control plane for Channel delivery operations."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.admin_deps import audit_control_plane, require_operator
from channels.stores import ChannelStore
from channels.connectors import (
    ConnectorAuthError,
    ConnectorError,
    WechatIlinkLoginClient,
)
from channels.inbound_runtime import ChannelInboundError
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
    platform = getattr(supervisor, "_channel_platform", None) if supervisor else None
    result["platforms"] = platform.snapshot() if platform is not None else {"running": False}
    audit_control_plane("channels.health", user, request)
    return result


@router.post("/webhooks/feishu/{channel_instance_id}")
async def feishu_webhook(channel_instance_id: str, request: Request):
    """Public Feishu callback; the configured Connector authenticates the body."""
    raw_body = await request.body()
    if len(raw_body) > 256_000:
        raise HTTPException(413, "Feishu webhook body is too large")
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Feishu webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Feishu webhook body must be an object")
    platforms = getattr(request.app.state, "channel_platform", None)
    if platforms is None:
        raise HTTPException(503, "Channel platform runtime is unavailable")
    try:
        result = await platforms.ingest_feishu(
            channel_instance_id,
            payload,
            raw_body=raw_body,
            headers=dict(request.headers),
        )
    except KeyError as exc:
        raise HTTPException(404, "Feishu channel instance is not configured") from exc
    except ConnectorAuthError as exc:
        raise HTTPException(401, "Feishu webhook authentication failed") from exc
    except ChannelInboundError as exc:
        raise HTTPException(403, "Feishu conversation is not authorized") from exc
    except ConnectorError as exc:
        raise HTTPException(400, "Feishu webhook payload is invalid") from exc
    if result.challenge is not None:
        return {"challenge": result.challenge}
    return {"code": 0}


@router.post("/wechat/login/qrcode")
async def wechat_login_qrcode(
    request: Request, response: Response, user=Depends(require_operator)
):
    client = WechatIlinkLoginClient()
    try:
        try:
            result = await client.get_qrcode()
        except ConnectorError as exc:
            raise HTTPException(502, "WeChat login service is unavailable") from exc
    finally:
        await client.close()
    response.headers["Cache-Control"] = "no-store"
    audit_control_plane("channels.wechat_login_qrcode", user, request)
    return result


@router.post("/wechat/login/status")
async def wechat_login_status(
    request: Request, response: Response, user=Depends(require_operator)
):
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "request body must be valid JSON") from exc
    qrcode_id = str(body.get("qrcode_id") or "").strip() if isinstance(body, dict) else ""
    if not qrcode_id:
        raise HTTPException(400, "qrcode_id is required")
    client = WechatIlinkLoginClient()
    try:
        try:
            status = await client.poll_qrcode(qrcode_id)
        except ConnectorError as exc:
            raise HTTPException(502, "WeChat login service is unavailable") from exc
    finally:
        await client.close()
    response.headers["Cache-Control"] = "no-store"
    audit_control_plane("channels.wechat_login_status", user, request, status=status.status)
    return {
        "status": status.status,
        "bot_token": status.bot_token,
        "bot_id": status.bot_id,
        "user_id": status.user_id,
        "base_url": status.base_url,
    }


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

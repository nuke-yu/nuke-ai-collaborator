"""Operator control plane for Channel delivery operations."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.admin_deps import audit_control_plane, require_operator
from api.deps import require_group_member_ready, require_group_owner
from db import global_db
from channels.bridge import (
    BindingStatus,
    ChannelBinding,
    ChannelBindingStore,
    ChannelIntegrationProvisioner,
    ChannelProvisioningConflict,
    IntegrationMemberStore,
)
from channels.core import canonical_channel_instance_id
from channels.stores import ChannelStore
from channels.connectors import (
    ConnectorAuthError,
    ConnectorError,
    WechatIlinkLoginClient,
)
from channels.inbound_runtime import ChannelInboundError
from runtime.dbpaths import channel_bridge_db_path


router = APIRouter(prefix="/api/channels", tags=["channels"])


class ChannelBindingCreateRequest(BaseModel):
    channel_instance_id: str
    external_tenant_id: str
    external_conversation_id: str
    default_bot_id: int
    allowed_bot_ids: list[int] = Field(default_factory=list)
    mention_required: bool = False
    inbound_policy: dict = Field(default_factory=dict)
    outbound_policy: dict = Field(default_factory=dict)


class ChannelBindingApprovalRequest(BaseModel):
    display_name: str
    avatar: str = ""
    metadata: dict = Field(default_factory=dict)


class ChannelBindingTransitionRequest(BaseModel):
    target: str


def _store() -> ChannelStore:
    return ChannelStore(channel_bridge_db_path())


def _binding_dict(binding: ChannelBinding) -> dict:
    value = binding.to_dict()
    value["status"] = str(binding.status)
    return value


def _delivery_runtime(request: Request):
    delivery = getattr(request.app.state, "channel_delivery", None)
    if delivery is None:
        raise HTTPException(503, "Channel delivery runtime is unavailable")
    return delivery


def _require_registered(request: Request, instance_id: str) -> str:
    canonical = canonical_channel_instance_id(instance_id)
    registered = set(_delivery_runtime(request).snapshot()["registered_channels"])
    if canonical not in registered:
        raise HTTPException(409, "Channel instance has no running Connector")
    return canonical


async def _binding_for_group(group_id: int, binding_id: str) -> ChannelBinding:
    binding = await ChannelBindingStore(channel_bridge_db_path()).get(binding_id)
    if binding is None or binding.group_id != group_id:
        raise HTTPException(404, "Channel binding not found")
    return binding


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


@router.get("/groups/{group_id}/bindings")
async def list_group_channel_bindings(
    group_id: int,
    user=Depends(require_group_member_ready),
):
    del user
    bindings = await ChannelBindingStore(channel_bridge_db_path()).list_for_group(group_id)
    members = await IntegrationMemberStore(channel_bridge_db_path()).list_for_group(group_id)
    member_by_binding = {member.binding_id: member.to_member_dict() for member in members}
    return {
        "group_id": group_id,
        "bindings": [
            {**_binding_dict(binding), "integration_member": member_by_binding.get(binding.binding_id)}
            for binding in bindings
        ],
    }


@router.post("/groups/{group_id}/bindings")
async def create_group_channel_binding(
    group_id: int,
    body: ChannelBindingCreateRequest,
    request: Request,
    user=Depends(require_group_owner),
):
    instance_id = _require_registered(request, body.channel_instance_id)
    bot_ids = {body.default_bot_id, *body.allowed_bot_ids}
    if any(bot_id <= 0 for bot_id in bot_ids):
        raise HTTPException(400, "Bot IDs must be positive")
    placeholders = ",".join("?" for _ in bot_ids)
    async with global_db() as conn:
        cursor = await conn.execute(
            f"SELECT id FROM members WHERE group_id=? AND type='bot' AND id IN ({placeholders})",
            (group_id, *sorted(bot_ids)),
        )
        existing = {int(row[0]) for row in await cursor.fetchall()}
    if existing != bot_ids:
        raise HTTPException(400, "default_bot_id and allowed_bot_ids must be Bots in this Group")
    try:
        binding = ChannelBinding(
            binding_id=str(uuid.uuid4()),
            channel_instance_id=instance_id,
            external_tenant_id=body.external_tenant_id,
            external_conversation_id=body.external_conversation_id,
            group_id=group_id,
            default_bot_id=body.default_bot_id,
            allowed_bot_ids=tuple(body.allowed_bot_ids),
            mention_required=body.mention_required,
            inbound_policy=body.inbound_policy,
            outbound_policy=body.outbound_policy,
            created_by=str(user["uid"]),
        )
        await ChannelBindingStore(channel_bridge_db_path()).create(binding)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit_control_plane(
        "channels.binding_create", user, request,
        group_id=group_id, binding_id=binding.binding_id,
        channel_instance_id=instance_id,
    )
    return _binding_dict(binding)


@router.post("/groups/{group_id}/bindings/{binding_id}/submit")
async def submit_group_channel_binding(
    group_id: int,
    binding_id: str,
    request: Request,
    user=Depends(require_group_owner),
):
    binding = await _binding_for_group(group_id, binding_id)
    _require_registered(request, binding.channel_instance_id)
    try:
        updated = await ChannelBindingStore(channel_bridge_db_path()).transition(
            binding_id, BindingStatus.PENDING_APPROVAL
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit_control_plane("channels.binding_submit", user, request, group_id=group_id, binding_id=binding_id)
    return _binding_dict(updated)


@router.post("/groups/{group_id}/bindings/{binding_id}/approve")
async def approve_group_channel_binding(
    group_id: int,
    binding_id: str,
    body: ChannelBindingApprovalRequest,
    request: Request,
    user=Depends(require_group_owner),
):
    binding = await _binding_for_group(group_id, binding_id)
    _require_registered(request, binding.channel_instance_id)
    try:
        approved, member = await ChannelIntegrationProvisioner(
            channel_bridge_db_path()
        ).approve(
            binding_id,
            display_name=body.display_name,
            avatar=body.avatar,
            metadata=body.metadata,
        )
    except (ValueError, ChannelProvisioningConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    audit_control_plane("channels.binding_approve", user, request, group_id=group_id, binding_id=binding_id)
    return {**_binding_dict(approved), "integration_member": member.to_member_dict()}


@router.post("/groups/{group_id}/bindings/{binding_id}/transition")
async def transition_group_channel_binding(
    group_id: int,
    binding_id: str,
    body: ChannelBindingTransitionRequest,
    request: Request,
    user=Depends(require_group_owner),
):
    binding = await _binding_for_group(group_id, binding_id)
    try:
        target = BindingStatus(body.target)
    except ValueError as exc:
        raise HTTPException(400, "unsupported Binding target state") from exc
    if target not in {BindingStatus.ACTIVE, BindingStatus.SUSPENDED, BindingStatus.REVOKED}:
        raise HTTPException(400, "use submit/approve for the approval transition")
    if target is BindingStatus.ACTIVE:
        _require_registered(request, binding.channel_instance_id)
    try:
        updated = await ChannelBindingStore(channel_bridge_db_path()).transition(
            binding_id, target
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit_control_plane(
        "channels.binding_transition", user, request,
        group_id=group_id, binding_id=binding_id, target=str(target),
    )
    return _binding_dict(updated)


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

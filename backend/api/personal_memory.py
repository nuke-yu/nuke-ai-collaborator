from fastapi import APIRouter, Depends, HTTPException
from core import auth
from db import global_db
from memory.canonical import (
    build_personal_knowledge_client, list_acl_audit_events, list_personal_apps,
    register_personal_app, set_personal_app_status, set_personal_access_rule,
    delete_personal_access_rule,
)
from memory.contracts import (CreatePersonalProjection, CreatePersonalRecord,
                              IngestPersonalKnowledge, MemoryAuthorizationError,
                              ObservePersonalHabit)
from memory.domain import MemoryScope, Principal
router=APIRouter()


def _personal_client(uid: int, group_ids=()):
    return build_personal_knowledge_client(Principal.user(uid, group_ids))


@router.get("/api/personal/memory/apps")
async def list_personal_memory_apps(include_inactive: bool = True, user=Depends(auth.get_current_user)):
    return {"apps": await list_personal_apps(user_id=int(user["uid"]), include_inactive=include_inactive)}


@router.post("/api/personal/memory/apps")
async def register_personal_memory_app(body: dict, user=Depends(auth.get_current_user)):
    try:
        await register_personal_app(
            user_id=int(user["uid"]), app_id=str(body["app_id"]), name=str(body["name"])
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return {"app_id": str(body["app_id"]), "status": "active"}


@router.post("/api/personal/memory/apps/{app_id}/status")
async def set_personal_memory_app_status(app_id: str, body: dict, user=Depends(auth.get_current_user)):
    if "active" not in body:
        raise HTTPException(400, "active is required")
    changed = await set_personal_app_status(
        user_id=int(user["uid"]), app_id=app_id, active=bool(body["active"])
    )
    if not changed:
        raise HTTPException(404, "Personal app not found")
    return {"app_id": app_id, "status": "active" if body["active"] else "inactive"}


@router.get("/api/personal/memory/audit")
async def list_personal_memory_audit(limit: int = 100, user=Depends(auth.get_current_user)):
    return {"events": await list_acl_audit_events(user_id=int(user["uid"]), limit=limit)}

@router.put("/api/personal/memory/access-rules")
async def set_personal_memory_access_rule(body: dict, user=Depends(auth.get_current_user)):
    try:
        await set_personal_access_rule(user_id=int(user["uid"]), **{
            key: str(body[key]) for key in ("subject_type", "subject_id", "object_type", "object_id", "action", "effect")
        })
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return {"status": "ok"}

@router.delete("/api/personal/memory/access-rules")
async def delete_personal_memory_access_rule(body: dict, user=Depends(auth.get_current_user)):
    try:
        deleted = await delete_personal_access_rule(user_id=int(user["uid"]), **{
            key: str(body[key]) for key in ("subject_type", "subject_id", "object_type", "object_id", "action")
        })
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    if not deleted:
        raise HTTPException(404, "Personal access rule not found")
    return {"status": "ok"}

@router.get("/api/personal/memory")
async def export_personal_memory(cursor: int = 0, limit: int = 1000, user=Depends(auth.get_current_user)):
    uid=int(user["uid"])
    try:
        return await _personal_client(uid).export(MemoryScope.personal(
            user_id=uid,actor_id=f"user:{uid}",purpose="personal_memory_export"),
            cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@router.post("/api/personal/memory/records")
async def create_personal_record(body:dict,user=Depends(auth.get_current_user)):
    try:
        uid=int(user["uid"])
        record_id=await _personal_client(uid).create_record(CreatePersonalRecord(
          scope=MemoryScope.personal(user_id=uid,actor_id=f"user:{uid}",purpose="personal_record_create"),
          kind=body["kind"],content=body["content"],source_type=body.get("source_type","manual"),
          source_id=body.get("source_id",""),speaker=body.get("speaker",user.get("sub","")),
          sensitivity=body.get("sensitivity","private")))
    except (KeyError,ValueError) as exc:raise HTTPException(400,str(exc))
    return {"record_id":record_id}

@router.post("/api/personal/memory/projections")
async def create_personal_projection(body:dict,user=Depends(auth.get_current_user)):
    gid=int(body.get("group_id",0)); bot_id=body.get("bot_id"); uid=int(user["uid"])
    async with global_db() as db:
        if bot_id is None:
            async with db.execute(
                "SELECT gm.group_id FROM group_memberships gm JOIN groups g ON g.id=gm.group_id "
                "WHERE gm.user_id=? AND gm.group_id=?", (uid,gid)
            ) as cur: valid=await cur.fetchone()
        else:
            async with db.execute(
                "SELECT gm.group_id FROM group_memberships gm JOIN members m ON m.group_id=gm.group_id "
                "WHERE gm.user_id=? AND gm.group_id=? AND m.id=? AND m.type='bot'",
                (uid,gid,bot_id)
            ) as cur: valid=await cur.fetchone()
    if not valid:raise HTTPException(404,"projection target not found")
    try:
        uid=int(user["uid"])
        projection_id=await _personal_client(uid, [int(valid[0])]).create_projection(CreatePersonalProjection(
          scope=MemoryScope.personal(user_id=uid,actor_id=f"user:{uid}",group_id=gid,
                                     purpose="personal_projection_create"),
          record_id=body["record_id"],target_group_id=gid,target_bot_id=bot_id,
          purpose=body.get("purpose","assistant_context"),expires_at=body.get("expires_at"),
              app_id=str(body.get("app_id") or "assistant_context")))
    except MemoryAuthorizationError as exc:raise HTTPException(403,str(exc))
    except (KeyError,ValueError) as exc:raise HTTPException(400,str(exc))
    return {"projection_id":projection_id}

@router.post("/api/personal/memory/sources")
async def ingest_personal_source(body:dict,user=Depends(auth.get_current_user)):
    try:
        uid=int(user["uid"])
        record_id=await _personal_client(uid).ingest(IngestPersonalKnowledge(
          scope=MemoryScope.personal(user_id=uid,actor_id=f"user:{uid}",purpose="personal_source_ingest"),
          kind=body["kind"],statement=body["statement"],source_type=body["source_type"],
          source_id=body["source_id"],speaker=body.get("speaker",""),
          subject=str(body.get("subject",uid)),context_kind=body.get("context_kind","general"),
          observed_at=body.get("observed_at"),asserted_by_user=bool(body.get("asserted_by_user",False)),
          sensitivity=body.get("sensitivity","private")))
    except (KeyError,ValueError) as exc:raise HTTPException(400,str(exc))
    return {"record_id":record_id}

@router.post("/api/personal/memory/habits")
async def observe_personal_habit(body:dict,user=Depends(auth.get_current_user)):
    try:
        uid=int(user["uid"])
        record_id=await _personal_client(uid).observe_habit(ObservePersonalHabit(
          scope=MemoryScope.personal(user_id=uid,actor_id=f"user:{uid}",purpose="personal_habit_observe"),
          habit_key=body["habit_key"],statement=body["statement"],source_type=body["source_type"],
          source_id=body["source_id"],context_kind=body["context_kind"],
          observed_at=int(body["observed_at"]),polarity=body.get("polarity","support")))
    except (KeyError,ValueError) as exc:raise HTTPException(400,str(exc))
    return {"record_id":record_id}

@router.post("/api/personal/memory/rebuild")
async def rebuild_personal_memory(user=Depends(auth.get_current_user)):
    uid=int(user["uid"])
    return await _personal_client(uid).rebuild(MemoryScope.personal(
        user_id=uid,actor_id=f"user:{uid}",purpose="personal_memory_rebuild"))

@router.delete("/api/personal/memory")
async def delete_personal_memory(user=Depends(auth.get_current_user)):
    uid=int(user["uid"])
    deleted=await _personal_client(uid).delete(MemoryScope.personal(
        user_id=uid,actor_id=f"user:{uid}",purpose="personal_memory_deletion"))
    return {"deleted":deleted}

@router.delete("/api/personal/memory/records/{record_id}")
async def delete_personal_record(record_id: str, user=Depends(auth.get_current_user)):
    uid=int(user["uid"])
    deleted=await _personal_client(uid).delete_record(MemoryScope.personal(
        user_id=uid,actor_id=f"user:{uid}",purpose="personal_record_deletion"), record_id)
    if not deleted:
        raise HTTPException(404, "Personal record not found")
    return {"status": "ok", "deleted_record_id": record_id}

@router.get("/api/personal/memory/records/{record_id}/impact")
async def personal_record_impact(record_id: str, user=Depends(auth.get_current_user)):
    uid = int(user["uid"])
    return await _personal_client(uid).get_record_impact(
        MemoryScope.personal(user_id=uid, actor_id=f"user:{uid}", purpose="personal_record_impact"),
        record_id,
    )

@router.delete("/api/personal/memory/projections/{projection_id}")
async def revoke_personal_projection(projection_id: str, user=Depends(auth.get_current_user)):
    uid=int(user["uid"])
    revoked=await _personal_client(uid).revoke_projection(MemoryScope.personal(
        user_id=uid,actor_id=f"user:{uid}",purpose="personal_projection_revocation"), projection_id)
    if not revoked:
        raise HTTPException(404, "Personal projection not found")
    return {"status": "ok", "revoked_projection_id": projection_id}

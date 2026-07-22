from fastapi import APIRouter, Depends, HTTPException
from core import auth
from db import global_db
from memory.bootstrap import build_personal_knowledge_client
from memory.contracts import (CreatePersonalProjection, CreatePersonalRecord,
                              IngestPersonalKnowledge, ObservePersonalHabit)
from memory.domain import MemoryScope

router=APIRouter()

@router.get("/api/personal/memory")
async def export_personal_memory(user=Depends(auth.get_current_user)):
    uid=int(user["uid"])
    return await build_personal_knowledge_client().export(MemoryScope.personal(
        user_id=uid,actor_id=f"user:{uid}",purpose="personal_memory_export"))

@router.post("/api/personal/memory/records")
async def create_personal_record(body:dict,user=Depends(auth.get_current_user)):
    try:
        uid=int(user["uid"])
        record_id=await build_personal_knowledge_client().create_record(CreatePersonalRecord(
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
        async with db.execute(
            "SELECT 1 FROM members WHERE group_id=? AND user_id=? AND type='user'",
            (gid, uid),
        ) as cur:
            user_member = await cur.fetchone()
        if not user_member:
            raise HTTPException(403, "Access denied: user is not a member of target group")
        if bot_id is None:
            async with db.execute("SELECT 1 FROM groups WHERE id=?",(gid,)) as cur: valid=await cur.fetchone()
        else:
            async with db.execute("SELECT 1 FROM members WHERE id=? AND group_id=? AND type='bot'",(bot_id,gid)) as cur: valid=await cur.fetchone()
    if not valid:raise HTTPException(404,"projection target not found")
    try:
        uid=int(user["uid"])
        projection_id=await build_personal_knowledge_client().create_projection(CreatePersonalProjection(
          scope=MemoryScope.personal(user_id=uid,actor_id=f"user:{uid}",group_id=gid,
                                     purpose="personal_projection_create"),
          record_id=body["record_id"],target_group_id=gid,target_bot_id=bot_id,
          purpose=body.get("purpose","assistant_context"),expires_at=body.get("expires_at")))
    except (KeyError,ValueError) as exc:raise HTTPException(400,str(exc))
    return {"projection_id":projection_id}

@router.post("/api/personal/memory/sources")
async def ingest_personal_source(body:dict,user=Depends(auth.get_current_user)):
    try:
        uid=int(user["uid"])
        record_id=await build_personal_knowledge_client().ingest(IngestPersonalKnowledge(
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
        record_id=await build_personal_knowledge_client().observe_habit(ObservePersonalHabit(
          scope=MemoryScope.personal(user_id=uid,actor_id=f"user:{uid}",purpose="personal_habit_observe"),
          habit_key=body["habit_key"],statement=body["statement"],source_type=body["source_type"],
          source_id=body["source_id"],context_kind=body["context_kind"],
          observed_at=int(body["observed_at"]),polarity=body.get("polarity","support")))
    except (KeyError,ValueError) as exc:raise HTTPException(400,str(exc))
    return {"record_id":record_id}

@router.post("/api/personal/memory/rebuild")
async def rebuild_personal_memory(user=Depends(auth.get_current_user)):
    uid=int(user["uid"])
    return await build_personal_knowledge_client().rebuild(MemoryScope.personal(
        user_id=uid,actor_id=f"user:{uid}",purpose="personal_memory_rebuild"))

@router.delete("/api/personal/memory")
async def delete_personal_memory(user=Depends(auth.get_current_user)):
    uid=int(user["uid"])
    deleted=await build_personal_knowledge_client().delete(MemoryScope.personal(
        user_id=uid,actor_id=f"user:{uid}",purpose="personal_memory_deletion"))
    return {"deleted":deleted}

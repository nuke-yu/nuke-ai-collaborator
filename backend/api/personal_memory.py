from fastapi import APIRouter, Depends, HTTPException
from core import auth
from db import global_db
from ai.personal_vault import add_record, delete_vault, export_vault, project, rebuild_vault

router=APIRouter()

@router.get("/api/personal/memory")
async def export_personal_memory(user=Depends(auth.get_current_user)):
    return await export_vault(int(user["uid"]))

@router.post("/api/personal/memory/records")
async def create_personal_record(body:dict,user=Depends(auth.get_current_user)):
    try:
        record_id=await add_record(user_id=int(user["uid"]),kind=body["kind"],content=body["content"],
          source_type=body.get("source_type","manual"),source_id=body.get("source_id",""),
          speaker=body.get("speaker",user.get("sub","")),subject=str(user["uid"]),authority="user_statement",
          sensitivity=body.get("sensitivity","private"),confidence=1.0,explicit=True)
    except (KeyError,ValueError) as exc:raise HTTPException(400,str(exc))
    return {"record_id":record_id}

@router.post("/api/personal/memory/projections")
async def create_personal_projection(body:dict,user=Depends(auth.get_current_user)):
    gid=int(body.get("group_id",0)); bot_id=body.get("bot_id")
    async with global_db() as db:
        if bot_id is None:
            async with db.execute("SELECT 1 FROM groups WHERE id=?",(gid,)) as cur: valid=await cur.fetchone()
        else:
            async with db.execute("SELECT 1 FROM members WHERE id=? AND group_id=? AND type='bot'",(bot_id,gid)) as cur: valid=await cur.fetchone()
    if not valid:raise HTTPException(404,"projection target not found")
    try:
        projection_id=await project(user_id=int(user["uid"]),record_id=body["record_id"],group_id=gid,
          bot_id=bot_id,purpose=body.get("purpose","assistant_context"),expires_at=body.get("expires_at"))
    except (KeyError,ValueError) as exc:raise HTTPException(400,str(exc))
    return {"projection_id":projection_id}

@router.post("/api/personal/memory/rebuild")
async def rebuild_personal_memory(user=Depends(auth.get_current_user)):
    return await rebuild_vault(int(user["uid"]))

@router.delete("/api/personal/memory")
async def delete_personal_memory(user=Depends(auth.get_current_user)):
    return {"deleted":await delete_vault(int(user["uid"]))}

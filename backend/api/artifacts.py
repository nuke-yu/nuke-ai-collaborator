from fastapi import APIRouter, HTTPException, Depends, Query
import db
from artifacts import get_artifact, list_artifacts, ArtifactNotFoundError
from runtime.dbpaths import group_db_path
from api.deps import ensure_group_ready

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/group/{group_id}", dependencies=[Depends(ensure_group_ready)])
async def api_list_group_artifacts(
    group_id: int,
    origin: str | None = Query(None, description="Filter by origin: upload, tool, workspace, workflow, connector"),
    session_id: str | None = Query(None, description="Filter by session ID"),
    bot_id: int | None = Query(None, description="Filter by bot ID"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    try:
        with db.bind_db(group_db_path(group_id)):
            items = await list_artifacts(
                group_id=group_id,
                origin=origin,
                session_id=session_id,
                bot_id=bot_id,
                limit=limit,
                offset=offset,
            )
            return [item.to_dict() for item in items]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{artifact_id}", dependencies=[Depends(ensure_group_ready)])
async def api_get_artifact(artifact_id: str, group_id: int | None = Query(None)):
    if not group_id:
        raise HTTPException(status_code=400, detail="Missing group_id query parameter")
    try:
        with db.bind_db(group_db_path(group_id)):
            item = await get_artifact(artifact_id, group_id=group_id)
            return item.to_dict()
    except ArtifactNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

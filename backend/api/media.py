"""Signed per-group media serving: GET /media/{gid}/{kind}/{filename}.

Self-authenticates via the HMAC signature in the query string (see core.media),
so — like the workspace preview router — it must be mounted WITHOUT the
header-based get_current_user dependency: an <img> tag cannot send a Bearer
header, but it can carry ?exp=…&sig=… that we minted for an authorized client.
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from core import media
from workspace import layout

router = APIRouter()


@router.get("/media/{gid}/{kind}/{filename}")
async def get_media(gid: int, kind: str, filename: str, exp: str = Query(...), sig: str = Query(...)):
    if not media.verify(gid, kind, filename, exp, sig):
        raise HTTPException(403, "Invalid or expired media signature")
    # verify() already rejected unsafe filenames / unknown kinds, but resolve and
    # re-check containment as defence in depth against path traversal.
    base = layout.group_media_dir(gid, kind).resolve()
    path = (base / filename).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(str(path))

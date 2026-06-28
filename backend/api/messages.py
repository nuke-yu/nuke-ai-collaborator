import uuid
import pathlib
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from db import global_db, get_unread_counts as _get_unread_counts
from workspace import layout
from core import media

UPLOAD_DIR = pathlib.Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml",
    "application/pdf", "text/plain", "application/json",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

router = APIRouter()


@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...), group_id: int = Form(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"不支持的文件类型: {file.content_type}")
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件大小超过 10MB 限制")
    ext = pathlib.Path(file.filename or "file").suffix
    filename = f"{uuid.uuid4()}{ext}"
    dest_dir = layout.group_media_dir(group_id, "uploads")
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / filename).write_bytes(contents)
    ref = media.canonical_ref(group_id, "uploads", filename)
    # `url` is the canonical ref to persist in the message; `preview_url` is a
    # freshly-signed URL for immediate client-side preview before the message is sent.
    return {"url": ref, "preview_url": media.presign(ref), "name": file.filename,
            "size": len(contents), "type": file.content_type}


@router.get("/api/members/{member_id}/unread")
async def get_unread_counts(member_id: int):
    # unread_counts is the supervisor-owned CENTRAL projection (incremented on new
    # messages for offline members, reset on read). Messages live in per-group DBs
    # the supervisor can't see, so we read the central projection, not messages.
    async with global_db() as db:
        return await _get_unread_counts(db, member_id)



import uuid
import pathlib
from fastapi import APIRouter, HTTPException, UploadFile, File
from db import get_db

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
async def upload_file(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"不支持的文件类型: {file.content_type}")
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件大小超过 10MB 限制")
    ext = pathlib.Path(file.filename or "file").suffix
    filename = f"{uuid.uuid4()}{ext}"
    (UPLOAD_DIR / filename).write_bytes(contents)
    return {"url": f"/uploads/{filename}", "name": file.filename,
            "size": len(contents), "type": file.content_type}


@router.get("/api/members/{member_id}/unread")
async def get_unread_counts(member_id: int):
    async with get_db() as db:
        async with db.execute("""
            SELECT m.group_id, COUNT(m.id) as unread
            FROM messages m
            LEFT JOIN member_read mr ON mr.member_id = ? AND mr.group_id = m.group_id
            WHERE m.id > COALESCE(mr.last_read_id, 0)
            GROUP BY m.group_id
        """, (member_id,)) as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}



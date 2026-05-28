from fastapi import APIRouter
from db import get_db
from models import RoleTemplateRequest

router = APIRouter()


@router.get("/api/templates")
async def get_templates():
    async with get_db() as db:
        async with db.execute("SELECT * FROM role_templates ORDER BY id") as cur:
            rows = await cur.fetchall()
    return [{"id": r[0], "name": r[1], "role": r[2], "system_prompt": r[3], "avatar_color": r[4]} for r in rows]


@router.post("/api/templates")
async def create_template(req: RoleTemplateRequest):
    async with get_db() as db:
        async with db.execute(
            "INSERT INTO role_templates (name, role, system_prompt, avatar_color) VALUES (?,?,?,?)",
            (req.name, req.role, req.system_prompt, req.avatar_color)
        ) as cur:
            await db.commit()
            return {"id": cur.lastrowid, "name": req.name, "role": req.role,
                    "system_prompt": req.system_prompt, "avatar_color": req.avatar_color}


@router.put("/api/templates/{template_id}")
async def update_template(template_id: int, req: RoleTemplateRequest):
    async with get_db() as db:
        await db.execute(
            "UPDATE role_templates SET name=?, role=?, system_prompt=?, avatar_color=? WHERE id=?",
            (req.name, req.role, req.system_prompt, req.avatar_color, template_id)
        )
        await db.commit()
    return {"id": template_id, "name": req.name, "role": req.role,
            "system_prompt": req.system_prompt, "avatar_color": req.avatar_color}


@router.delete("/api/templates/{template_id}")
async def delete_template(template_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM role_templates WHERE id=?", (template_id,))
        await db.commit()
    return {"ok": True}

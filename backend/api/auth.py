from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import db
from core import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])

class AuthRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(AuthRequest):
    email: str = None

@router.post("/register")
async def register(req: RegisterRequest):
    async with db.global_db() as cdb:
        # Check if user exists
        async with cdb.execute("SELECT id FROM users WHERE username = ?", (req.username,)) as cur:
            if await cur.fetchone():
                raise HTTPException(400, "用户名已存在")
        
        pw_hash = auth.hash_password(req.password)
        await cdb.execute(
            "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)",
            (req.username, pw_hash, req.email)
        )
        await cdb.commit()
        
    return {"ok": True}

@router.post("/login")
async def login(req: AuthRequest):
    async with db.global_db() as cdb:
        async with cdb.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (req.username,)) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(401, "用户名或密码错误")
            
            uid, uname, stored_hash = row
            if not auth.verify_password(req.password, stored_hash):
                raise HTTPException(401, "用户名或密码错误")
                
    token = auth.create_token(uid, uname)
    return {"token": token, "user": {"id": uid, "username": uname}}

@router.get("/me")
async def me(user = Depends(auth.get_current_user)):
    return user

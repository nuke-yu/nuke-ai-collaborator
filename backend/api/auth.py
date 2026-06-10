from collections import defaultdict
import time
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
import db
from core import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])

_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60   # seconds
_RATE_MAX = 5       # attempts per window

def _check_rate_limit(key: str) -> None:
    now = time.time()
    attempts = [t for t in _login_attempts[key] if now - t < _RATE_WINDOW]
    _login_attempts[key] = attempts
    if len(attempts) >= _RATE_MAX:
        raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
    _login_attempts[key].append(now)

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
async def login(req: AuthRequest, request: Request):
    _check_rate_limit(request.client.host if request.client else req.username)
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

@router.post("/refresh")
async def refresh(user = Depends(auth.get_current_user)):
    token = auth.create_token(user["uid"], user["sub"])
    return {"token": token, "user": {"id": user["uid"], "username": user["sub"]}}

@router.get("/me")
async def me(user = Depends(auth.get_current_user)):
    return user

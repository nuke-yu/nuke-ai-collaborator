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
        # Serialize registration so two concurrent first sign-ups cannot both
        # receive the bootstrap operator role.
        await cdb.execute("BEGIN IMMEDIATE")
        # Check if user exists
        async with cdb.execute("SELECT id FROM users WHERE username = ?", (req.username,)) as cur:
            if await cur.fetchone():
                raise HTTPException(400, "用户名已存在")
        
        pw_hash = auth.hash_password(req.password)
        async with cdb.execute("SELECT COUNT(*) FROM users") as cur:
            is_first_user = (await cur.fetchone())[0] == 0
        await cdb.execute(
            "INSERT INTO users (username, password_hash, email, is_operator) VALUES (?, ?, ?, ?)",
            (req.username, pw_hash, req.email, is_first_user)
        )
        await cdb.commit()
        
    return {"ok": True}

@router.post("/login")
async def login(req: AuthRequest, request: Request):
    _check_rate_limit(request.client.host if request.client else req.username)
    async with db.global_db() as cdb:
        async with cdb.execute("SELECT id, username, password_hash, is_operator FROM users WHERE username = ?", (req.username,)) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(401, "用户名或密码错误")
            
            uid, uname, stored_hash, operator_flag = row
            if not auth.verify_password(req.password, stored_hash):
                raise HTTPException(401, "用户名或密码错误")
                
    is_operator = bool(operator_flag)
    token = auth.create_token(uid, uname, is_operator)
    return {"token": token, "user": {"id": uid, "username": uname, "is_operator": is_operator}}

@router.post("/refresh")
async def refresh(user = Depends(auth.get_current_user)):
    async with db.global_db() as cdb:
        async with cdb.execute(
            "SELECT username, is_operator FROM users WHERE id = ?", (user["uid"],)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(401, "用户不存在")
    username, operator_flag = row
    is_operator = bool(operator_flag)
    token = auth.create_token(user["uid"], username, is_operator)
    return {"token": token, "user": {"id": user["uid"], "username": username, "is_operator": is_operator}}

@router.get("/me")
async def me(user = Depends(auth.get_current_user)):
    return user

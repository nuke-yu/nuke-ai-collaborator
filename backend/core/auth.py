import hashlib
import hmac
import json
import base64
import time
import os
import logging
from typing import Optional

log = logging.getLogger(__name__)

SECRET_KEY = os.environ.get("AUTH_SECRET", "super-secret-key-change-me")
TOKEN_TTL = 86400 * 7 # 1 week


def _operator_usernames() -> set[str]:
    raw = os.environ.get("NUKE_OPERATOR_USERS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _operator_ids() -> set[int]:
    raw = os.environ.get("NUKE_OPERATOR_IDS", "")
    ids = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except Exception:
            log.warning("Ignoring invalid NUKE_OPERATOR_IDS entry: %r", item)
    return ids

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return base64.b64encode(salt + dk).decode()

def verify_password(password: str, hashed: str) -> bool:
    try:
        decoded = base64.b64decode(hashed)
        salt = decoded[:16]
        stored_dk = decoded[16:]
        new_dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        return hmac.compare_digest(stored_dk, new_dk)
    except Exception:
        return False

def create_token(user_id: int, username: str) -> str:
    payload = {
        "uid": user_id,
        "sub": username,
        "exp": int(time.time()) + TOKEN_TTL
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip('=')
    
    signature = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip('=')
    
    return f"{payload_b64}.{sig_b64}"

def verify_token(token: str) -> Optional[dict]:
    try:
        payload_b64, sig_b64 = token.split('.')
        
        # Verify signature
        expected_sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest()
        actual_sig = base64.urlsafe_b64decode(sig_b64 + '=' * (4 - len(sig_b64) % 4))
        
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
            
        # Decode payload
        payload_json = base64.urlsafe_b64decode(payload_b64 + '=' * (4 - len(payload_b64) % 4)).decode()
        payload = json.loads(payload_json)
        
        # Check expiry
        if payload.get("exp", 0) < time.time():
            return None
            
        return payload
    except Exception:
        return None


def is_operator(user: dict | None) -> bool:
    """Control-plane authorization predicate.

    Runtime sources, in priority order:
    1. Explicit test/dev payload marker: {"is_operator": True}
    2. Username allow-list via NUKE_OPERATOR_USERS
    3. User-id allow-list via NUKE_OPERATOR_IDS
    """
    if not isinstance(user, dict):
        return False
    if user.get("is_operator") is True:
        return True
    if str(user.get("sub") or "") in _operator_usernames():
        return True
    try:
        uid = int(user.get("uid"))
    except Exception:
        uid = None
    return uid in _operator_ids() if uid is not None else False

# FastAPI Dependency
from fastapi import Header, HTTPException, Depends

async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid token")
    
    token = authorization.split(" ")[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
        
    return payload

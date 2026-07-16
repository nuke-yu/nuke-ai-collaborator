import hashlib
import hmac
import json
import base64
import time
import os
import logging
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_AUTH_SECRET = "super-secret-key-change-me"
_MIN_SECRET_LENGTH = 32
SECRET_KEY = os.environ.get("AUTH_SECRET", _DEFAULT_AUTH_SECRET)
_is_production = os.environ.get("NUKE_ENV", "").lower() == "production"
_secret_is_default = SECRET_KEY == _DEFAULT_AUTH_SECRET
_secret_is_weak = not SECRET_KEY or len(SECRET_KEY.strip()) < _MIN_SECRET_LENGTH

if _is_production and (_secret_is_default or _secret_is_weak):
    raise RuntimeError(
        f"FATAL: AUTH_SECRET is {'not set' if _secret_is_default else 'too short (minimum ' + str(_MIN_SECRET_LENGTH) + ' chars)'} "
        f"and NUKE_ENV=production. Refusing to start — all tokens would be "
        f"forgeable. Set AUTH_SECRET to a strong random value (≥{_MIN_SECRET_LENGTH} chars)."
    )
if _secret_is_default:
    log.critical(
        "AUTH_SECRET is not set — using built-in default key. "
        "All tokens are forgeable by anyone who reads the source code. "
        "Set AUTH_SECRET env var before deploying to any non-dev environment."
    )
TOKEN_TTL = 86400 * 7 # 1 week


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

def create_token(user_id: int, username: str, is_operator: bool = False) -> str:
    payload = {
        "uid": user_id,
        "sub": username,
        "is_operator": bool(is_operator),
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
    """Return the signed operator claim issued from the users table."""
    return isinstance(user, dict) and user.get("is_operator") is True

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

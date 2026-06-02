"""API-key / provider config endpoints (backs the frontend ApiKeyManager).

Keys are stored in backend/app_config.json (git-ignored); get_key() falls back to
the matching env var (DEEPSEEK_API_KEY, OPENAI_API_KEY, ...) when the file has none.
"""
from fastapi import APIRouter

from config import read_config, write_config, get_key, _preview, FIELDS

router = APIRouter()


def _status() -> dict:
    # Reflect the EFFECTIVE value (file first, then env var) so the UI shows a key
    # as configured even when it's provided via the environment.
    out = {}
    for field in FIELDS:
        val = get_key(field)
        out[field] = {"configured": bool(val), "preview": _preview(val)}
    return out


@router.get("/api/config")
async def get_config():
    return _status()


@router.put("/api/config")
async def save_config(data: dict):
    cfg = read_config()
    for field in FIELDS:
        if field in data:                      # only touch fields the client sent
            val = (data.get(field) or "").strip()
            if val:
                cfg[field] = val
            else:
                cfg.pop(field, None)           # empty value clears the key
    write_config(cfg)
    return _status()

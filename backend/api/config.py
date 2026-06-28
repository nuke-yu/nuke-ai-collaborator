"""API-key / provider config endpoints (backs the frontend ApiKeyManager).

Keys are stored in backend/app_config.json (git-ignored); get_key() falls back to
the matching env var (DEEPSEEK_API_KEY, OPENAI_API_KEY, ...) when the file has none.
"""
from fastapi import APIRouter, HTTPException
import json
from pathlib import Path

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


def resolve_mcp_config_path() -> Path:
    import os
    env_path = os.environ.get("MCP_SERVERS_CONFIG")
    if env_path:
        return Path(env_path)
    return Path(__file__).parent.parent / "mcp_servers.json"


@router.get("/api/config/mcp")
async def get_mcp_config():
    path = resolve_mcp_config_path()
    if not path.exists():
        return {"mcpServers": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mcpServers": {}}


@router.put("/api/config/mcp")
async def save_mcp_config(data: dict):
    path = resolve_mcp_config_path()
    if not isinstance(data, dict) or "mcpServers" not in data:
        raise HTTPException(400, "Invalid MCP config format. Must contain 'mcpServers'.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise HTTPException(500, f"Failed to save MCP config file: {e}")

    # Notify MCP collector process of the change
    try:
        from runtime.supervisor import supervisor
        import runtime.ipc as ipc
        if supervisor:
            await supervisor.send_to_worker_id(
                ipc.protocol.MCP_COLLECTOR_ID,
                {"type": "mcp_reload"}
            )
    except Exception:
        pass

    return data

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


def _mask_mcp_config(config: dict) -> dict:
    import copy
    cfg = copy.deepcopy(config)
    mcp_servers = cfg.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return cfg
    for server_cfg in mcp_servers.values():
        if not isinstance(server_cfg, dict):
            continue
        env = server_cfg.get("env")
        if isinstance(env, dict):
            for k, v in env.items():
                if isinstance(v, str):
                    env[k] = _preview(v)
        headers = server_cfg.get("headers")
        if isinstance(headers, dict):
            for k, v in headers.items():
                if isinstance(v, str):
                    headers[k] = _preview(v)
    return cfg


def _unmask_mcp_config(new_config: dict, old_config: dict) -> dict:
    import copy
    cfg = copy.deepcopy(new_config)
    new_servers = cfg.get("mcpServers")
    old_servers = old_config.get("mcpServers")
    if not isinstance(new_servers, dict) or not isinstance(old_servers, dict):
        return cfg
    for server_name, new_server in new_servers.items():
        if not isinstance(new_server, dict):
            continue
        old_server = old_servers.get(server_name)
        if not isinstance(old_server, dict):
            continue
        new_env = new_server.get("env")
        old_env = old_server.get("env")
        if isinstance(new_env, dict) and isinstance(old_env, dict):
            for k, new_val in new_env.items():
                if k in old_env and isinstance(new_val, str) and isinstance(old_env[k], str):
                    old_val = old_env[k]
                    if new_val == _preview(old_val):
                        new_env[k] = old_val
        new_headers = new_server.get("headers")
        old_headers = old_server.get("headers")
        if isinstance(new_headers, dict) and isinstance(old_headers, dict):
            for k, new_val in new_headers.items():
                if k in old_headers and isinstance(new_val, str) and isinstance(old_headers[k], str):
                    old_val = old_headers[k]
                    if new_val == _preview(old_val):
                        new_headers[k] = old_val
    return cfg


@router.get("/api/config/mcp")
async def get_mcp_config():
    path = resolve_mcp_config_path()
    if not path.exists():
        return {"mcpServers": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return _mask_mcp_config(data)
    except Exception:
        return {"mcpServers": {}}


@router.put("/api/config/mcp")
async def save_mcp_config(data: dict):
    path = resolve_mcp_config_path()
    if not isinstance(data, dict) or "mcpServers" not in data:
        raise HTTPException(400, "Invalid MCP config format. Must contain 'mcpServers'.")
    old_config = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                old_config = json.load(f)
        except Exception:
            pass
    unmasked_data = _unmask_mcp_config(data, old_config)
    import tempfile
    import os
    import logging
    logger = logging.getLogger(__name__)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=str(path.parent), prefix="mcp_config_tmp_")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(unmasked_data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, str(path))
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
    except Exception as e:
        raise HTTPException(500, f"Failed to save MCP config file: {e}")

    warning = None
    # Notify MCP collector process of the change
    try:
        from runtime.supervisor import supervisor
        import runtime.ipc as ipc
        if supervisor:
            await supervisor.send_to_worker_id(
                ipc.protocol.MCP_COLLECTOR_ID,
                {"type": "mcp_reload"}
            )
        else:
            warning = "Supervisor not running, collector reload skipped."
            logger.warning("save_mcp_config: supervisor is None, cannot notify collector.")
    except Exception as e:
        warning = f"Failed to notify collector: {e}"
        logger.warning(f"save_mcp_config: failed to notify collector: {e}")

    res = _mask_mcp_config(unmasked_data)
    if warning:
        res["warning"] = warning
    return res

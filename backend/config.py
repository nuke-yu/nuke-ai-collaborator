import json
import os
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("NUKE_APP_CONFIG_PATH")
    or Path(__file__).parent / "app_config.json"
)

FIELDS = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "ollama_base_url": "OLLAMA_BASE_URL",
    "minimax_api_key": "MINIMAX_API_KEY",
    "zhipu_api_key": "ZHIPU_API_KEY",
    "qwen_api_key": "QWEN_API_KEY",
}

def read_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}

def write_config(data: dict):
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def get_key(field: str) -> str:
    """Single source of truth: app_config.json.

    Env vars are migrated into the file once at startup (bootstrap_from_env), so the
    file is authoritative and the frontend fully controls every key — no hidden
    env/file dual source to drift out of sync.
    """
    return (read_config().get(field) or "").strip()


def bootstrap_from_env() -> None:
    """One-time migration: copy any env-provided keys into app_config.json for fields
    the file doesn't already define, so the file becomes the single editable source.
    Called once on app startup."""
    cfg = read_config()
    changed = False
    for field, env_var in FIELDS.items():
        if not (cfg.get(field) or "").strip():
            env_val = (os.getenv(env_var) or "").strip()
            if env_val:
                cfg[field] = env_val
                changed = True
    if changed:
        write_config(cfg)

def _preview(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return "*" * len(val)
    return val[:3] + "···" + val[-4:]

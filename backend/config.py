import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "app_config.json"

FIELDS = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "ollama_base_url": "OLLAMA_BASE_URL",
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
    """Return config value, falling back to the corresponding env var."""
    cfg = read_config()
    val = cfg.get(field, "").strip()
    if val:
        return val
    env_var = FIELDS.get(field, "")
    return os.getenv(env_var, "")

def _preview(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return "*" * len(val)
    return val[:3] + "···" + val[-4:]

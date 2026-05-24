import importlib.util
import logging
from pathlib import Path

from executors.base import BotExecutor

logger = logging.getLogger(__name__)

_registry: dict[str, BotExecutor] = {}
PLUGIN_DIR = Path(__file__).parent / "plugins"


def _load_file(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error(f"Plugin load failed [{path.name}]: {e}")
        return
    for attr in dir(module):
        cls = getattr(module, attr)
        if (
            isinstance(cls, type)
            and issubclass(cls, BotExecutor)
            and cls is not BotExecutor
            and getattr(cls, "executor_id", "")
        ):
            instance = cls()
            _registry[instance.executor_id] = instance
            instance.register_tools()
            logger.info(f"Registered plugin: {instance.executor_id}")


def discover():
    """Scan plugins/ and load all non-private .py files."""
    _registry.clear()
    for f in sorted(PLUGIN_DIR.glob("*.py")):
        if not f.name.startswith("_"):
            _load_file(f)


def reload() -> list[str]:
    """Hot reload — rescan and re-import all plugins. No restart needed."""
    discover()
    return list(_registry.keys())


def get(executor_id: str) -> BotExecutor:
    """Return the executor for the given id, falling back to simple_v1."""
    return _registry.get(executor_id) or _registry.get("simple_v1") or next(iter(_registry.values()))


def all_plugins() -> list[dict]:
    return [p.info() for p in _registry.values()]

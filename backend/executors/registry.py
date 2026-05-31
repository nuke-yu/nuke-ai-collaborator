import importlib.util
import logging
from pathlib import Path

from executors.base import BotExecutor

logger = logging.getLogger(__name__)

_registry: dict[str, BotExecutor] = {}
_failures: dict[str, str] = {}
PLUGIN_DIR = Path(__file__).parent / "plugins"


def _load_file(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        _failures[path.name] = f"{type(e).__name__}: {e}"
        logger.error(f"Plugin load failed [{path.name}]: {e}", exc_info=True)
        return
    _failures.pop(path.name, None)
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
    """Scan plugins/ and load all non-private .py files.

    Idempotent: re-discovery (startup AND /api/plugins/reload) re-execs plugin
    modules, whose fresh hook function objects would bypass tool_executor's
    identity-based dedup and ACCUMULATE duplicate before/after hooks (e.g. the
    permission check running twice). Clear the hooks first so register_tools()
    rebuilds exactly one set; tool defs/handlers are keyed by name and overwrite,
    so they need no reset.
    """
    _registry.clear()
    _failures.clear()
    from executors import tool_executor
    tool_executor.clear_before_hooks()
    tool_executor.clear_after_hooks()
    for f in sorted(PLUGIN_DIR.glob("*.py")):
        if not f.name.startswith("_"):
            _load_file(f)


def failures() -> dict[str, str]:
    """Return plugin files that failed to import, keyed by filename."""
    return dict(_failures)


def reload() -> list[str]:
    """Hot reload — rescan and re-import all plugins. No restart needed."""
    discover()
    return list(_registry.keys())


def get(executor_id: str) -> BotExecutor:
    """Return the executor for the given id, falling back to tool_loop_v1."""
    executor = _registry.get(executor_id) or _registry.get("tool_loop_v1")
    if executor is not None:
        return executor
    if _registry:
        return next(iter(_registry.values()))
    detail = "; ".join(f"{k}: {v}" for k, v in _failures.items()) or "no plugins found"
    raise RuntimeError(f"No executors registered (plugin load failures: {detail})")


def all_plugins() -> list[dict]:
    return [p.info() for p in _registry.values()]

import importlib.util
import logging
import sys
from pathlib import Path

from executors.base import BotExecutor

logger = logging.getLogger(__name__)

_registry: dict[str, BotExecutor] = {}
_disposers: dict[str, object] = {}
from executors.container import DependencyContainer
_container = DependencyContainer()


def configure_dependency(name: str, value: object) -> None:
    _container.bind(name, value)


def clear_dependencies() -> None:
    global _container
    _container = DependencyContainer()
_failures: dict[str, str] = {}
PLUGIN_DIR = Path(__file__).parent / "plugins"


def get_external_plugins_dir() -> Path | None:
    import os
    env_dir = os.environ.get("NUKE_EXTERNAL_PLUGINS_DIR")
    if env_dir:
        return Path(env_dir)
    ws_root = os.environ.get("NUKE_WORKSPACE_ROOT")
    if ws_root:
        return Path(ws_root).parent / "plugins"
    return Path(__file__).parent.parent.parent / "workspaces" / "plugins"


def _load_file(path: Path):
    parent_dir = str(path.parent.resolve())
    if parent_dir not in sys.path:
        sys.path.append(parent_dir)

    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so module-level introspection that
    # resolves `cls.__module__` works — notably @dataclass's KW_ONLY check does
    # `sys.modules.get(cls.__module__).__dict__`, which crashes if the module
    # isn't registered (esp. with `from __future__ import annotations`).
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        sys.modules.pop(spec.name, None)
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
            instance.dependencies = _container.resolve_many(
                getattr(instance.manifest, "inject", ())
            )
            _registry[instance.executor_id] = instance
            from executors import tool_executor
            disposer = tool_executor.Disposer()
            try:
                with tool_executor.registration_scope(disposer):
                    instance.register_tools()
            except Exception:
                disposer.dispose()
                raise
            _disposers[instance.executor_id] = disposer
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
    for disposer in _disposers.values():
        disposer.dispose()
    _disposers.clear()
    tool_executor.clear_before_hooks()
    tool_executor.clear_after_hooks()
    
    # 1. Load built-in plugins
    for f in sorted(PLUGIN_DIR.glob("*.py")):
        if not f.name.startswith("_"):
            _load_file(f)
            
    # 2. Load external plugins
    ext_dir = get_external_plugins_dir()
    if ext_dir and ext_dir.exists() and ext_dir.is_dir():
        logger.info(f"Scanning external plugins from: {ext_dir}")
        for f in sorted(ext_dir.glob("*.py")):
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

"""
core/orchestration/registry.py — 编排器注册表

和 executors/registry.py 同构：内置 workflow_v1（DeclarativeOrchestrator），
并扫描 plugins/ 目录加载自定义编排器（逃生舱 —— 丢一个 .py 即可加新拓扑）。
"""
import importlib.util
import logging
from pathlib import Path

from core.orchestration.base import Orchestrator
from core.orchestration.declarative import DeclarativeOrchestrator

logger = logging.getLogger(__name__)

_registry: dict[str, Orchestrator] = {}
PLUGIN_DIR = Path(__file__).parent / "plugins"


def _register(instance: Orchestrator) -> None:
    if getattr(instance, "orchestrator_id", ""):
        _registry[instance.orchestrator_id] = instance
        logger.info(f"Registered orchestrator: {instance.orchestrator_id}")


def _load_file(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error(f"Orchestrator plugin load failed [{path.name}]: {e}")
        return
    for attr in dir(module):
        cls = getattr(module, attr)
        if (
            isinstance(cls, type)
            and issubclass(cls, Orchestrator)
            and cls is not Orchestrator
            and getattr(cls, "orchestrator_id", "")
        ):
            _register(cls())


def discover():
    """注册内置编排器，再扫描 plugins/ 加载自定义的。"""
    _registry.clear()
    _register(DeclarativeOrchestrator())
    if PLUGIN_DIR.is_dir():
        for f in sorted(PLUGIN_DIR.glob("*.py")):
            if not f.name.startswith("_"):
                _load_file(f)


def reload() -> list[str]:
    discover()
    return list(_registry.keys())


def get(orchestrator_id: str = "workflow_v1") -> Orchestrator:
    """按 id 取编排器，缺省回退 workflow_v1。"""
    if not _registry:
        discover()
    return _registry.get(orchestrator_id) or _registry["workflow_v1"]


def all_plugins() -> list[dict]:
    if not _registry:
        discover()
    return [o.info() for o in _registry.values()]

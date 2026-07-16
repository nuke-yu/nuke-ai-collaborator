"""
plugins/host.py — Plugin Host (插件宿主)

为插件提供标准化的基础设施访问接口。插件通过 host 获取能力，
不直接 import 或修改核心模块。

职责：
  1. 插件发现与加载（扫描 plugins/ 目录）
  2. 事件观察（订阅 Supervisor 广播事件流）
  3. 路由挂载（在 FastAPI 上挂载插件路由）
  4. Worker 决策（读取 Worker 负载、选最闲 Worker）
  5. 任务操作（创建/中止/重试任务群组）
  6. 生命周期管理（启动/停止/卸载插件）

设计原则：
  - 插件只通过 host 接口操作，不直接触碰 supervisor/app/core 模块
  - host 可以控制每个插件的权限范围
  - 卸载插件 = host.unload(name)，所有注册点自动清理
"""
import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from runtime.ipc.protocol import MCP_COLLECTOR_ID

log = logging.getLogger(__name__)


class PluginHost:
    """插件宿主：给插件提供标准化的基础设施访问。"""

    def __init__(self, app, supervisor):
        """
        Args:
            app: FastAPI application instance
            supervisor: runtime.supervisor.Supervisor instance
        """
        self._app = app
        self._sup = supervisor
        self._plugins: dict[str, dict] = {}  # name → {module, routers, observers, bg_tasks}
        self._plugin_dir: Optional[Path] = None

    # ── 发现与加载 ─────────────────────────────────────────────────────

    async def discover_and_load(self) -> None:
        """扫描插件目录，加载所有含 register(host) 入口的插件包。

        插件目录优先级：
          1. NUKE_PLUGINS_DIR 环境变量
          2. backend/plugins/ 默认目录（本文件所在目录）
        """
        env_dir = os.getenv("NUKE_PLUGINS_DIR")
        if env_dir:
            self._plugin_dir = Path(env_dir)
        else:
            self._plugin_dir = Path(__file__).parent

        if not self._plugin_dir.exists():
            log.debug("PluginHost: no plugins directory at %s", self._plugin_dir)
            return

        # Ensure plugins dir is importable
        plugins_parent = str(self._plugin_dir.parent)
        if plugins_parent not in sys.path:
            sys.path.insert(0, plugins_parent)

        loaded = []
        for item in sorted(self._plugin_dir.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue
            init_file = item / "__init__.py"
            if not init_file.exists():
                continue

            try:
                module = importlib.import_module(f"plugins.{item.name}")
                if hasattr(module, "register"):
                    await self._load_plugin(item.name, module)
                    loaded.append(item.name)
            except Exception:
                log.exception("PluginHost: failed to load plugin %s", item.name)

        if loaded:
            log.info("PluginHost: loaded plugins: %s", ", ".join(loaded))
        else:
            log.debug("PluginHost: no plugins found in %s", self._plugin_dir)

    async def _load_plugin(self, name: str, module) -> None:
        """Load a single plugin by calling its register(host)."""
        # Register the plugin record before calling register() so the plugin
        # can use host methods that reference self._plugins[name].
        self._plugins[name] = {
            "module": module,
            "routers": [],
            "observers": [],
            "bg_tasks": [],
        }
        try:
            await module.register(self)
            log.info("PluginHost: plugin '%s' registered successfully", name)
        except Exception:
            # Rollback: unload any partial registrations
            log.exception("PluginHost: plugin '%s' register() failed, rolling back", name)
            await self.unload_plugin(name)
            raise

    async def unload_plugin(self, name: str) -> None:
        """Stop and remove a plugin: cancel bg tasks, remove observers, clear record."""
        record = self._plugins.pop(name, None)
        if not record:
            return

        # Cancel background tasks
        for task in record.get("bg_tasks", []):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Remove event observers
        for obs_name in record.get("observers", []):
            self._sup.unregister_observer(obs_name)

        # Note: FastAPI doesn't support removing routers after startup.
        # Routers remain mounted but the plugin's handlers will fail gracefully
        # since the plugin state is gone. For a full unload, a restart is needed.
        log.info("PluginHost: plugin '%s' unloaded", name)

    async def unload_all(self) -> None:
        """Unload all plugins during shutdown."""
        for name in list(self._plugins.keys()):
            await self.unload_plugin(name)

    # ── 能力 1：事件观察 ─────────────────────────────────────────────

    def observe_events(self, plugin_name: str, callback: Callable) -> None:
        """订阅所有群组的广播事件流。

        callback 签名: (group_id: int, payload: dict) -> None
        callback 必须是非阻塞的（推队列即返回），慢回调不影响核心流程。

        Args:
            plugin_name: 观察者名称（用于注册和注销）
            callback: 同步回调函数
        """
        self._sup.register_observer(plugin_name, callback)
        if plugin_name in self._plugins:
            self._plugins[plugin_name]["observers"].append(plugin_name)

    # ── 能力 2：Worker 决策 ──────────────────────────────────────────

    def pick_worker(self, strategy: str = "least_loaded") -> Optional[str]:
        """按策略选一个 Worker ID。

        Strategies:
          - "least_loaded": 选 active_tasks 最少的 Worker
          - "random": 随机选
          - "modulo": 按 group_id 取模（默认路由）

        Returns:
            worker_id string (e.g. "w0"), or None if no workers available
        """
        # ``Supervisor._workers`` contains every connected runtime peer, including
        # the MCP collector.  The collector can execute MCP calls but cannot host
        # groups or agent workflows, so it must never enter task scheduling.
        workers = [
            worker_id
            for worker_id in self._sup._workers
            if worker_id != MCP_COLLECTOR_ID
        ]
        if not workers:
            return None

        if strategy == "least_loaded":
            def load_key(worker_id: str) -> tuple[bool, int, int]:
                stats = self._sup._worker_stats.get(worker_id)
                active_tasks = (
                    stats.get("bg", {}).get("active_tasks") if stats else None
                )
                active_groups = (
                    stats.get("lifecycle", {}).get("active_groups_count", 0)
                    if stats else 0
                )
                # Prefer workers that have reported health.  A newly connected
                # worker remains a fallback, but missing telemetry is not treated
                # as an artificial zero load.  Group count breaks equal-task ties
                # so sequential coding jobs spread across otherwise idle workers.
                return active_tasks is None, active_tasks or 0, active_groups

            return min(workers, key=load_key)
        elif strategy == "random":
            import random
            return random.choice(workers)
        else:
            # Fallback to first available
            return workers[0]

    def get_worker_stats(self) -> dict:
        """Read-only access to all worker stats. Returns {worker_id: stats_dict}."""
        return dict(self._sup._worker_stats)

    # ── 能力 3：路由挂载 ─────────────────────────────────────────────

    def mount_router(self, router, prefix: str = "", **kwargs) -> None:
        """在 FastAPI 上挂载插件的 REST/WS 路由。

        Args:
            router: FastAPI APIRouter instance
            prefix: URL prefix (e.g. "/api/agent")
            **kwargs: passed to app.include_router()
        """
        self._app.include_router(router, prefix=prefix, **kwargs)
        # Track for potential unload (note: FastAPI can't remove routers at runtime)
        plugin_name = self._current_plugin_name()
        if plugin_name and plugin_name in self._plugins:
            self._plugins[plugin_name]["routers"].append(router)

    # ── 能力 4：任务操作 ─────────────────────────────────────────────

    async def reassign_group(self, group_id: int, worker_id: str) -> None:
        """Move a group to a specific worker (uses existing reassign mechanism)."""
        await self._sup.reassign_group(group_id, worker_id)

    # ── 能力 5：后台任务 ─────────────────────────────────────────────

    def start_background(self, coro, plugin_name: str = None) -> asyncio.Task:
        """Start a background task tracked by the plugin host.

        The task will be cancelled when the plugin is unloaded.
        """
        task = asyncio.create_task(coro)
        name = plugin_name or self._current_plugin_name()
        if name and name in self._plugins:
            self._plugins[name]["bg_tasks"].append(task)
        return task

    # ── 能力 6：Supervisor 访问（只读）───────────────────────────────

    @property
    def supervisor(self):
        """Read-only access to the Supervisor instance (for advanced use)."""
        return self._sup

    @property
    def app(self):
        """Read-only access to the FastAPI app."""
        return self._app

    # ── 内部工具 ─────────────────────────────────────────────────────

    def _current_plugin_name(self) -> Optional[str]:
        """Infer which plugin is currently calling based on call stack.

        This is a best-effort helper for auto-tracking. Plugins can explicitly
        pass their name to avoid relying on stack inspection.
        """
        import inspect
        frame = inspect.currentframe()
        try:
            # Walk up the stack looking for a frame inside a plugin module
            caller = frame
            while caller:
                module_name = caller.f_globals.get("__name__", "")
                if module_name.startswith("plugins.") and module_name != "plugins.host":
                    # Extract plugin name: plugins.agent_dashboard.xxx → agent_dashboard
                    parts = module_name.split(".")
                    if len(parts) >= 2 and parts[1] in self._plugins:
                        return parts[1]
                caller = caller.f_back
        finally:
            del frame
        return None

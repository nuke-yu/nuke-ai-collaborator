"""Skill hot-reload watcher.

Watches the workspaces/ directory for skill file changes and broadcasts
a `skills_changed` WebSocket event so the frontend panel auto-refreshes.

Uses watchdog (runs in a background thread) + asyncio.run_coroutine_threadsafe
to schedule the async broadcast on the main event loop.
"""
import re
import threading
import asyncio
import logging
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from . import constants as _const

log = logging.getLogger(__name__)

_DEBOUNCE_SECS = 0.3

# Matches paths inside any skills/ subdirectory we care about
# bot 私有技能现位于 group_{gid}/bots/bot_{id}/skills/（嵌套），group_id=捕获组1，member_id=捕获组2
_BOT_RE    = re.compile(r"^group_(\d+)[/\\]bots[/\\]bot_(\d+)[/\\]skills[/\\]")
_GROUP_RE  = re.compile(r"^group_(\d+)[/\\]shared[/\\]skills[/\\]")
_SYSTEM_RE = re.compile(r"^system[/\\]skills[/\\]")
_ROLE_RE   = re.compile(r"^roles[/\\][^/\\]+[/\\]skills[/\\]")


def _parse_path(abs_path: str) -> dict | None:
    """Return change info dict or None if path is not a skill file."""
    p = Path(abs_path)
    if p.suffix not in (".md", ".py"):
        return None
    try:
        rel = str(p.relative_to(_const.WORKSPACE_ROOT)).replace("\\", "/")   # 实时读取，容忍重绑定
    except ValueError:
        return None

    m = _BOT_RE.match(rel)
    if m:
        return {"source": "bot", "member_id": int(m.group(2)), "group_id": int(m.group(1))}

    m = _GROUP_RE.match(rel)
    if m:
        return {"source": "group", "member_id": None, "group_id": int(m.group(1))}

    if _SYSTEM_RE.match(rel):
        return {"source": "system", "member_id": None, "group_id": None}

    if _ROLE_RE.match(rel):
        return {"source": "role", "member_id": None, "group_id": None}

    return None


class _SkillEventHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _debounce(self, key: str, info: dict):
        """Cancel any pending timer for key, then schedule a new one."""
        with self._lock:
            if key in self._timers:
                self._timers[key].cancel()
            info_snap = dict(info)
            t = threading.Timer(_DEBOUNCE_SECS, self._fire, args=(info_snap,))
            self._timers[key] = t
            t.start()

    def _fire(self, info: dict):
        asyncio.run_coroutine_threadsafe(self._broadcast(info), self._loop)

    async def _broadcast(self, info: dict):
        from ws_manager import manager  # lazy import — avoids startup ordering issues
        payload = {
            "type": "skills_changed",
            "member_id": info.get("member_id"),
            "source": info["source"],
        }
        group_id = info.get("group_id")
        if group_id is not None:
            await manager.broadcast(group_id, payload)
        else:
            # Bot / system / role changes: notify all connected groups
            for gid in list(manager.connections.keys()):
                await manager.broadcast(gid, payload)
        log.debug("skills_changed broadcast: %s", payload)

    def _handle(self, path: str):
        info = _parse_path(path)
        if info is None:
            return
        # Same-process fast-path: drop the four-layer scan cache immediately so a
        # subsequent list_skills_all in THIS process sees the change without
        # waiting for the mtime signature to diverge. (Correctness across
        # processes is guaranteed by the signature, not by this hook.)
        try:
            from .discovery import invalidate_skills_cache
            invalidate_skills_cache()
        except Exception:
            log.warning("skills.watcher: failed to invalidate skills cache for %s", path, exc_info=True)
        key = f"{info['source']}:{info.get('member_id') or info.get('group_id')}"
        self._debounce(key, info)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._handle(event.dest_path)


class SkillWatcher:
    """Lifecycle manager for the watchdog observer thread."""

    def __init__(self):
        self._observer: Observer | None = None

    def start(self, loop: asyncio.AbstractEventLoop):
        root = _const.WORKSPACE_ROOT
        if not root.exists():
            root.mkdir(parents=True, exist_ok=True)
        handler = _SkillEventHandler(loop)
        self._observer = Observer()
        self._observer.schedule(handler, str(root), recursive=True)
        self._observer.start()
        log.info("SkillWatcher started watching %s", root)

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            log.info("SkillWatcher stopped")


watcher = SkillWatcher()

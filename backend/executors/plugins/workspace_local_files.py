"""Identity-scoped, symlink-safe local file access primitives."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def local_file_roots(context: dict, *, allow_write: bool, layout) -> list[Path]:
    roots = [layout.bot_dir(context["group_id"], context["bot_id"]), layout.group_shared_dir(context["group_id"])]
    if not allow_write:
        roots.extend(Path(p) for p in context.get("authorized_read_roots", ()))
    return roots


def lexical_relative_to_root(path: str, roots: list[Path]):
    requested = Path(os.path.abspath(os.path.expanduser(path)))
    matches = []
    for root in roots:
        declared = Path(os.path.abspath(str(root.expanduser())))
        try:
            relative = requested.relative_to(declared)
        except ValueError:
            continue
        parts = tuple(part for part in relative.parts if part not in ("", "."))
        if not any(part == ".." for part in parts):
            matches.append((root, parts))
    return max(matches, key=lambda item: len(item[0].parts), default=None)


def validate_path(path: str, context: dict | None, *, allow_write: bool, layout):
    ctx = context or {}
    if ctx.get("group_id") is None:
        return None, "[安全拒绝] 无法确定群组上下文，拒绝文件访问"
    if ctx.get("bot_id") is None:
        return None, "[安全拒绝] 无法确定 bot_id，拒绝文件访问"
    selected = lexical_relative_to_root(path, local_file_roots(ctx, allow_write=allow_write, layout=layout))
    if selected is None:
        return None, f"[安全拒绝] {'写入' if allow_write else '读取'}路径不在当前 bot 的授权目录内：{path}"
    if not selected[1]:
        return None, f"[安全拒绝] 文件路径不能是目录根：{path}"
    return selected, None


def secure_open_root(root: Path, layout) -> int:
    if not all(hasattr(os, attr) for attr in ("O_NOFOLLOW", "O_DIRECTORY")):
        raise OSError("当前平台不支持安全的 dir_fd/O_NOFOLLOW 文件访问")
    base = Path(os.path.abspath(str(layout._root().expanduser())))
    declared = Path(os.path.abspath(str(root.expanduser())))
    relative = declared.relative_to(base)
    canonical = base.resolve(strict=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(canonical.anchor, flags)
    try:
        for part in canonical.parts[1:] + relative.parts:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd
    except Exception:
        os.close(fd); raise


def secure_open_parent(root: Path, parts: tuple[str, ...], *, create: bool, layout):
    fd = secure_open_root(root, layout)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        for part in parts[:-1]:
            if create:
                try: os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError: pass
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd, parts[-1]
    except Exception:
        os.close(fd); raise


def read_local_file_sync(spec, *, layout) -> str:
    root, parts = spec
    parent, name = secure_open_parent(root, parts, create=False, layout=layout)
    try: fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally: os.close(parent)
    with open(fd, "r", encoding="utf-8", closefd=True) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise OSError("拒绝读取非普通文件或硬链接文件")
        return stream.read()


def write_local_file_sync(spec, content: str, *, layout) -> None:
    root, parts = spec; parent, name = secure_open_parent(root, parts, create=True, layout=layout); fd = None
    try:
        try: fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError: fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise OSError("拒绝写入非普通文件或硬链接文件")
        os.ftruncate(fd, 0)
        with open(fd, "w", encoding="utf-8", closefd=True) as stream: fd = None; stream.write(content)
    finally:
        if fd is not None: os.close(fd)
        os.close(parent)

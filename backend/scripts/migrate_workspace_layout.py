"""一次性工作区布局迁移（Phase 2）。

把 bot 私有区从扁平 `workspaces/bot_{id}` 收归到嵌套 `workspaces/group_{gid}/bots/bot_{id}`。

安全约定：
- **跑前停机 + 备份** `workspaces/`（脚本不替你备份）。
- 默认 `--dry-run`：只打印计划，不动磁盘。加 `--apply` 才执行。
- 无 DB 记录的 `bot_*` 目录视为脏数据，默认仅列出；加 `--delete-orphans` 才删。
- 幂等：目标已存在 / 源已不在 → 跳过，可重复运行。
- 收尾 `verify()`：每个 DB bot 恰有一个嵌套目录，且无残留扁平 `bot_*`（孤儿除外）。

用法：
    python3 -m scripts.migrate_workspace_layout            # dry-run，打印计划
    python3 -m scripts.migrate_workspace_layout --apply    # 执行
    python3 -m scripts.migrate_workspace_layout --apply --delete-orphans
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

_FLAT_BOT_RE = re.compile(r"^bot_(\d+)$")


def _flat_bot_ids(root: Path) -> set[int]:
    """磁盘上现存的扁平 bot_{id} 目录的 id 集合。"""
    ids: set[int] = set()
    if not root.exists():
        return ids
    for p in root.iterdir():
        if p.is_dir():
            m = _FLAT_BOT_RE.match(p.name)
            if m:
                ids.add(int(m.group(1)))
    return ids


def plan_migration(root: Path, bots: list[tuple[int, int]]):
    """返回 (moves, already, orphans)。

    moves:   需要移动的 (bot_id, group_id)（扁平存在且嵌套目标不存在）。
    already: 目标已存在的 (bot_id, group_id)（幂等跳过）。
    orphans: 磁盘上有扁平目录但 DB 无记录的 bot_id。
    """
    flat = _flat_bot_ids(root)
    known = {bid for bid, _ in bots}

    moves, already = [], []
    for bot_id, gid in bots:
        src = root / f"bot_{bot_id}"
        dst = root / f"group_{gid}" / "bots" / f"bot_{bot_id}"
        if dst.exists():
            already.append((bot_id, gid))
        elif src.exists():
            moves.append((bot_id, gid))
        # 源、目标都不存在：该 bot 还没工作区，跳过

    orphans = sorted(flat - known)
    return moves, already, orphans


def apply_migration(root: Path, bots: list[tuple[int, int]], *,
                    dry_run: bool = True, delete_orphans: bool = False) -> dict:
    """执行迁移。dry_run=True 时只规划不动盘。返回报告 dict。"""
    moves, already, orphans = plan_migration(root, bots)

    if not dry_run:
        for bot_id, gid in moves:
            src = root / f"bot_{bot_id}"
            dst = root / f"group_{gid}" / "bots" / f"bot_{bot_id}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        if delete_orphans:
            for bot_id in orphans:
                shutil.rmtree(root / f"bot_{bot_id}", ignore_errors=True)

    return {
        "moved": moves,          # dry-run 时为「计划移动」，apply 时为「已移动」
        "already": already,
        "orphans": orphans,
        "dry_run": dry_run,
        "deleted_orphans": orphans if (not dry_run and delete_orphans) else [],
    }


def verify(root: Path, bots: list[tuple[int, int]]) -> tuple[bool, list[str]]:
    """收尾校验：每个 DB bot 恰有一个嵌套目录；无残留扁平 bot_*（孤儿除外）。"""
    problems: list[str] = []
    known = {bid for bid, _ in bots}

    for bot_id, gid in bots:
        dst = root / f"group_{gid}" / "bots" / f"bot_{bot_id}"
        flat = root / f"bot_{bot_id}"
        if not dst.exists():
            problems.append(f"缺嵌套目录: {dst}")
        if flat.exists():
            problems.append(f"残留扁平目录: {flat}")

    # 已知 bot 之外的扁平目录是孤儿（允许保留，单独提示，不算失败）
    leftover = sorted(_flat_bot_ids(root) - known)
    if leftover:
        problems.append(f"孤儿扁平目录（未删）: {[f'bot_{i}' for i in leftover]}")
        # 孤儿不导致 ok=False —— 它们是刻意保留项
        ok = all(not p.startswith(("缺嵌套", "残留")) for p in problems)
        return ok, problems

    return (len(problems) == 0), problems


def _load_bots_from_db() -> list[tuple[int, int]]:
    """从中央 DB 读所有 bot 的 (id, group_id)。"""
    from db import connect_sync
    with connect_sync() as conn:
        rows = conn.execute(
            "SELECT id, group_id FROM members WHERE type = 'bot'"
        ).fetchall()
    return [(int(r[0]), int(r[1])) for r in rows]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    delete_orphans = "--delete-orphans" in argv

    from skills.constants import WORKSPACE_ROOT
    root = Path(WORKSPACE_ROOT)

    print(f"[迁移] 工作区根: {root}")
    print(f"[迁移] 模式: {'APPLY（写盘）' if apply else 'DRY-RUN（只打印）'}"
          f"{'  + 删除孤儿' if delete_orphans else ''}")
    if apply:
        print("[迁移] 确认：已停机且已备份 workspaces/ ？(按 Ctrl-C 取消)")

    bots = _load_bots_from_db()
    report = apply_migration(root, bots, dry_run=not apply, delete_orphans=delete_orphans)

    print(f"\n  待移动/已移动 ({len(report['moved'])}):")
    for bid, gid in report["moved"]:
        print(f"    bot_{bid} → group_{gid}/bots/bot_{bid}")
    print(f"  幂等跳过（目标已存在, {len(report['already'])}): {[b for b, _ in report['already']]}")
    print(f"  孤儿（无 DB 记录, {len(report['orphans'])}): {[f'bot_{i}' for i in report['orphans']]}")
    if report["deleted_orphans"]:
        print(f"  已删除孤儿: {[f'bot_{i}' for i in report['deleted_orphans']]}")

    if apply:
        ok, problems = verify(root, bots)
        print(f"\n[校验] {'通过 ✓' if ok else '发现问题 ✗'}")
        for p in problems:
            print(f"    - {p}")
        return 0 if ok else 1
    else:
        print("\n[迁移] dry-run 完成。确认无误后加 --apply 执行。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Backfill legacy Chroma Fact/Reflection records into canonical SQLite.

Run with the application stopped so the paginated Chroma snapshot is stable.
The command is dry-run by default and never scans every Group implicitly:

    python3 -m scripts.backfill_canonical_bot_memory --group-id 7
    python3 -m scripts.backfill_canonical_bot_memory --group-id 7 --apply
    python3 -m scripts.backfill_canonical_bot_memory \
        --group-id 7 --bot-id 3 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from memory.bootstrap import (
    build_canonical_chroma_backfill_client,
    memory_module,
)
from runtime.dbpaths import group_db_path


async def run_backfill(
    *,
    group_ids: tuple[int, ...],
    apply: bool,
    bot_ids: frozenset[int] = frozenset(),
    batch_size: int = 500,
) -> list[dict[str, Any]]:
    if not group_ids:
        raise ValueError("at least one explicit group_id is required")
    if any(group_id <= 0 for group_id in group_ids):
        raise ValueError("group_ids must contain positive integers")
    if any(bot_id <= 0 for bot_id in bot_ids):
        raise ValueError("bot_ids must contain positive integers")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    client = build_canonical_chroma_backfill_client()
    reports: list[dict[str, Any]] = []
    for group_id in dict.fromkeys(group_ids):
        path = group_db_path(group_id)
        if not os.path.isfile(path):
            reports.append({
                "group_id": group_id,
                "dry_run": not apply,
                "skipped": "group_db_missing",
            })
            continue
        try:
            if apply:
                await memory_module().ensure_group(group_id)
            report = await client.backfill(
                group_id,
                dry_run=not apply,
                bot_ids=bot_ids,
                batch_size=batch_size,
            )
        except Exception as exc:
            reports.append({
                "group_id": group_id,
                "dry_run": not apply,
                "error_type": type(exc).__name__,
            })
        else:
            reports.append(report.as_dict())
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Chroma Bot facts/reflections into canonical SQLite",
    )
    parser.add_argument(
        "--group-id",
        type=_positive_int,
        action="append",
        required=True,
        help="explicit target Group; repeat for multiple Groups",
    )
    parser.add_argument(
        "--bot-id",
        type=_positive_int,
        action="append",
        help="optional Bot filter applied within every selected Group",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=500,
        help="bounded Chroma page size (default: 500)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write canonical records; default is dry-run",
    )
    args = parser.parse_args(argv)
    reports = asyncio.run(run_backfill(
        group_ids=tuple(args.group_id),
        apply=args.apply,
        bot_ids=frozenset(args.bot_id or ()),
        batch_size=args.batch_size,
    ))
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] canonical Bot memory backfill")
    for report in reports:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if any("error_type" in report for report in reports) else 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())

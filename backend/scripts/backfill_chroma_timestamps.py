"""One-time data migration: backfill missing `timestamp` metadata on Chroma memories.

Memories written before the recency-decay refactor have no `timestamp`, so the
retrieval ranker treats them as ~30 days old and pushes them to the bottom. This
script reads each memory's source message `created_at` (from the per-group DB the
memory belongs to, keyed by its `group_id` metadata) and stamps the vector with a
UTC epoch — the same basis `add_to_chroma` uses for new writes.

Usage (from the backend/ directory):

    python3 -m scripts.backfill_chroma_timestamps             # apply
    python3 -m scripts.backfill_chroma_timestamps --dry-run   # report only, no writes

Stop the app first so nothing writes to chroma mid-migration. Idempotent:
records that already have a timestamp are skipped, so re-running is safe.
"""
import argparse
import asyncio
import sys

from ai.memory import backfill_chroma_timestamps


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Chroma memory timestamps.")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    stats = asyncio.run(backfill_chroma_timestamps(dry_run=args.dry_run))

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] Chroma timestamp backfill")
    print(f"  scanned           : {stats['scanned']}")
    print(f"  missing timestamp : {stats['need']}")
    print(f"  {'would update' if args.dry_run else 'updated':<17} : {stats['updated']}")
    print(f"  no group_id       : {stats['no_group']}")
    print(f"  skipped (no msg)  : {stats['skipped_no_msg']}")

    if stats["no_group"]:
        print(
            f"\nNote: {stats['no_group']} legacy record(s) lack a group_id. They are looked up "
            "best-effort against the central DB (which holds no messages), so they typically "
            "keep the ranker's default decay — acceptable, as they are genuinely old.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

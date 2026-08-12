"""One-time data migration: backfill missing `scored_by_model` metadata on Chroma memories.

Memories written before salience scoring started recording its source model have no
`scored_by_model`. The field does not affect retrieval, but it is the only way to later
tell which stored salience scores came from which model. If a group ever switches its
scoring model, the two scales coexist in one ranking pool and old scores skew the results;
recovering them requires re-normalizing the memories the *old* model scored — which is
impossible if we never recorded which model that was.

This script stamps every memory lacking `scored_by_model` with a single placeholder label
(default "legacy/unknown"), preserving that escape hatch for the existing corpus.

Usage (from the backend/ directory):

    python3 -m scripts.backfill_chroma_scored_by_model              # apply
    python3 -m scripts.backfill_chroma_scored_by_model --dry-run    # report only, no writes
    python3 -m scripts.backfill_chroma_scored_by_model --label deepseek/deepseek-chat

Stop the app first so nothing writes to chroma mid-migration. Idempotent: records that
already have a scored_by_model are skipped, so re-running is safe.
"""
import argparse
import asyncio

from memory.adapters.projections.maintenance import backfill_scored_by_model


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Chroma memory scored_by_model.")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument(
        "--label",
        default="legacy/unknown",
        help="placeholder model label to stamp on memories missing scored_by_model "
        "(use the actual provider/model if you know the whole corpus was scored by one)",
    )
    args = parser.parse_args(argv)

    stats = asyncio.run(backfill_scored_by_model(dry_run=args.dry_run, label=args.label))

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] Chroma scored_by_model backfill (label={args.label!r})")
    print(f"  scanned              : {stats['scanned']}")
    print(f"  missing field        : {stats['need']}")
    print(f"  {'would update' if args.dry_run else 'updated':<20} : {stats['updated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

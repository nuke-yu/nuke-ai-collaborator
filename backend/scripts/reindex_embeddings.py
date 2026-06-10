"""DFT-035: rebuild the chroma vector index with the currently-configured model.

When you change ``NUKE_EMBEDDING_PROVIDER`` / ``NUKE_EMBEDDING_MODEL``, the
stored vectors (built with the previous model) become dimension-incompatible and
``ai/memory.py`` will refuse to load them. This script re-embeds the existing
documents with the new model and re-stamps the collection signature.

Usage (from the backend/ directory):

    python3 -m scripts.reindex_embeddings            # reindex to the configured model
    python3 -m scripts.reindex_embeddings --dry-run  # show what would happen

Stop the app first so nothing writes to chroma mid-reindex.
"""
import argparse
import sys

from ai import embeddings
from core import config

CHROMA_PATH = "./chroma_db"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reindex chroma embeddings (DFT-035).")
    parser.add_argument("--path", default=CHROMA_PATH, help="chroma persistent dir")
    parser.add_argument("--dry-run", action="store_true", help="report target, do not rewrite")
    args = parser.parse_args(argv)

    target_sig = embeddings.embedding_signature()
    print(f"Configured embedding: provider={config.EMBEDDING_PROVIDER} "
          f"model={config.EMBEDDING_MODEL or '(default)'} → signature '{target_sig}'")
    print(f"Chroma path: {args.path}")

    if args.dry_run:
        print("--dry-run: no changes written.")
        return 0

    # Build the target embedding function (validates provider/key up front).
    try:
        new_ef = embeddings.get_embedding_function()
    except embeddings.EmbeddingConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print("Reindexing (re-embedding all stored documents with the new model)...")
    n = embeddings.reindex_collection(args.path, new_ef=new_ef, new_sig=target_sig)
    print(f"Done. Reindexed {n} document(s); collection stamped '{target_sig}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

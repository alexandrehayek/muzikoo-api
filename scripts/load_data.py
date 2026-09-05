#!/usr/bin/env python
"""Load data/dataset.json into the tracks table, chunk by chunk.

    python scripts/load_data.py                    # full load, appends
    python scripts/load_data.py --truncate         # full load, clean slate
    python scripts/load_data.py --limit 1000       # smoke test
    python scripts/load_data.py --dry-run          # parse only, no database
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muzikoo.config import get_settings
from muzikoo.ingest import load_dataset


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=settings.dataset_path, help="JSON Lines dataset")
    parser.add_argument("--chunk-size", type=int, default=settings.chunk_size)
    parser.add_argument("--truncate", action="store_true", help="empty the table first")
    parser.add_argument("--limit", type=int, default=None, help="stop after N rows")
    parser.add_argument("--dry-run", action="store_true", help="parse and transform only")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"dataset not found: {args.path}", file=sys.stderr)
        return 1

    size_gb = args.path.stat().st_size / 1e9
    print(f"loading {args.path} ({size_gb:.2f} GB), chunk_size={args.chunk_size}", file=sys.stderr)

    stats = load_dataset(
        args.path,
        chunk_size=args.chunk_size,
        truncate=args.truncate,
        limit=args.limit,
        dry_run=args.dry_run,
    )

    print(stats.summary(), file=sys.stderr)
    if stats.malformed_lines:
        print(f"malformed lines: {stats.malformed_lines}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Apply the SQL schema and/or the indexes.

    python scripts/init_db.py --stage schema    # before loading
    python scripts/init_db.py --stage indexes   # after loading
    python scripts/init_db.py                   # both

Indexes are a separate stage because building them once after the bulk load is
much faster than maintaining them across ~500k inserts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muzikoo.config import PROJECT_ROOT
from muzikoo.db import apply_sql_file, check_connection, count_tracks, raw_connection

STAGES = {"schema": "sql/001_schema.sql", "indexes": "sql/002_indexes.sql"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=[*STAGES, "all"], default="all")
    parser.add_argument("--drop", action="store_true", help="DROP the tracks table first (destroys data)")
    args = parser.parse_args()

    try:
        version = check_connection()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot connect to PostgreSQL: {exc}", file=sys.stderr)
        print("check DB_* settings in .env, and that sql/000_bootstrap.sql has been run", file=sys.stderr)
        return 1
    print(f"connected: {version.split(',')[0]}", file=sys.stderr)

    stages = list(STAGES) if args.stage == "all" else [args.stage]
    with raw_connection() as conn:
        if args.drop:
            print("dropping table tracks ...", file=sys.stderr)
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS tracks")
        for stage in stages:
            path = PROJECT_ROOT / STAGES[stage]
            print(f"applying {stage}: {path.relative_to(PROJECT_ROOT)} ...", file=sys.stderr)
            apply_sql_file(path, connection=conn)

    print(f"done. tracks table holds {count_tracks():,} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

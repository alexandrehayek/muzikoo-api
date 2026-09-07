#!/usr/bin/env python
"""Verify a production database is ready to serve, before pointing Vercel at it.

    DATABASE_URL='postgresql://...' python scripts/check_prod.py

Checks the things that actually break a Supabase restore: row count, the
indexes the API depends on, pg_trgm, and that one query of each API shape
returns rows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

EXPECTED_INDEXES = {
    "tracks_track_lower_idx",
    "tracks_artist_lower_idx",
    "tracks_track_trgm_idx",
    "tracks_artist_trgm_idx",
    "tracks_emotion_popularity_idx",
    "tracks_best_for_gin_idx",
}


def main() -> int:
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set — nothing to check", file=sys.stderr)
        return 1

    from muzikoo import repository
    from muzikoo.config import get_settings
    from muzikoo.db import get_engine

    settings = get_settings()
    # Never print the password.
    host = settings.database_url.split("@")[-1]
    print(f"target: {host}")
    print(f"serverless pooling: {settings.is_serverless}")

    failures: list[str] = []

    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT count(*) FROM tracks")).scalar_one()
        print(f"rows: {rows:,}")
        if rows == 0:
            failures.append("tracks table is empty")

        size = conn.execute(
            text("SELECT pg_size_pretty(pg_total_relation_size('tracks'))")
        ).scalar_one()
        print(f"table size: {size}")

        has_trgm = conn.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")
        ).scalar_one()
        print(f"pg_trgm: {'yes' if has_trgm else 'NO'}")
        if not has_trgm:
            failures.append("pg_trgm missing — track.search will fall back to seq scans")

        present = {
            r[0] for r in conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'tracks'")
            )
        }
        missing = EXPECTED_INDEXES - present
        print(f"indexes: {len(present & EXPECTED_INDEXES)}/{len(EXPECTED_INDEXES)}")
        if missing:
            failures.append(f"missing indexes: {', '.join(sorted(missing))}")

        lyrics_present = conn.execute(
            text("SELECT count(*) FROM tracks WHERE lyrics IS NOT NULL")
        ).scalar_one()
        print(f"rows with lyrics: {lyrics_present:,}" + ("  (slim transfer)" if not lyrics_present else ""))

    # One query per API shape, through the same code the API uses.
    search, search_total = repository.search_tracks("love", limit=1)
    emotion, emotion_total = repository.tracks_by_emotion("joy", limit=1)
    best_for, best_for_total = repository.tracks_best_for("party", limit=1)
    print(f"track.search   -> {search_total:,} results")
    print(f"track.byemotion-> {emotion_total:,} results")
    print(f"track.bestfor  -> {best_for_total:,} results")
    for label, got in (("search", search), ("byemotion", emotion), ("bestfor", best_for)):
        if not got:
            failures.append(f"track.{label} returned no rows")

    if failures:
        print("\nFAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

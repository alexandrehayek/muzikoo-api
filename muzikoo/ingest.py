"""Stream the dataset into PostgreSQL in chunks.

``data/dataset.json`` is 1.3 GB and is JSON *Lines* despite the extension: one
complete JSON object per line. So it is read line by line and never held in
memory — peak memory is one chunk of rows (~13 MB at chunk_size=5000), not the
file.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from psycopg2.extras import execute_values

from .db import raw_connection
from .transform import COLUMNS, SkipRecord, transform_record

INSERT_SQL = f"INSERT INTO tracks ({', '.join(COLUMNS)}) VALUES %s"


@dataclass
class LoadStats:
    lines_read: int = 0
    inserted: int = 0
    malformed: int = 0          # line was not valid JSON
    skipped: int = 0            # valid JSON, but unusable as a row
    malformed_lines: list[int] = field(default_factory=list)
    seconds: float = 0.0

    def summary(self) -> str:
        rate = self.inserted / self.seconds if self.seconds else 0.0
        return (
            f"read {self.lines_read:,} lines | inserted {self.inserted:,} | "
            f"malformed {self.malformed} | skipped {self.skipped} | "
            f"{self.seconds:.1f}s ({rate:,.0f} rows/s)"
        )


def iter_json_lines(path: Path, stats: LoadStats) -> Iterator[dict]:
    """Yield one decoded object per line, counting (not raising on) bad lines.

    The dataset has exactly one corrupt line (427,560), where the "Album" pair
    was replaced by a stray `f`. One bad row out of 498k should not abort a
    20-minute load, so it is recorded and skipped.
    """
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            stats.lines_read += 1
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                stats.malformed += 1
                if len(stats.malformed_lines) < 20:
                    stats.malformed_lines.append(lineno)


def chunked(iterable: Iterable, size: int) -> Iterator[list]:
    chunk: list = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def load_dataset(
    path: Path,
    *,
    chunk_size: int = 5000,
    truncate: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    progress_every: int = 10,
    log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr, flush=True),
) -> LoadStats:
    """Read `path` line by line and insert rows chunk by chunk.

    limit    — stop after this many inserted rows (for smoke tests).
    dry_run  — parse and transform everything, touch no database.
    """
    stats = LoadStats()
    started = time.perf_counter()

    def rows() -> Iterator[tuple]:
        for record in iter_json_lines(path, stats):
            try:
                yield transform_record(record)
            except SkipRecord:
                stats.skipped += 1

    if dry_run:
        for n, chunk in enumerate(chunked(rows(), chunk_size), start=1):
            stats.inserted += len(chunk)
            if n % progress_every == 0:
                log(f"  [dry-run] {stats.inserted:,} rows transformed")
            if limit and stats.inserted >= limit:
                break
        stats.seconds = time.perf_counter() - started
        return stats

    with raw_connection() as conn:
        with conn.cursor() as cur:
            # Bulk-load session tuning: the load is one long sequence of
            # inserts, so waiting on fsync per chunk is pure overhead. A crash
            # mid-load means re-running the load, which is already the recovery
            # path. Session-scoped, so nothing leaks to the API's connections.
            cur.execute("SET synchronous_commit = off")
            if truncate:
                log("  truncating tracks ...")
                cur.execute("TRUNCATE TABLE tracks RESTART IDENTITY")

            for n, chunk in enumerate(chunked(rows(), chunk_size), start=1):
                if limit is not None:
                    room = limit - stats.inserted
                    if room <= 0:
                        break
                    chunk = chunk[:room]
                execute_values(cur, INSERT_SQL, chunk, page_size=len(chunk))
                stats.inserted += len(chunk)
                conn.commit()  # keep the transaction (and WAL) small
                if n % progress_every == 0:
                    elapsed = time.perf_counter() - started
                    log(
                        f"  {stats.inserted:,} rows inserted "
                        f"({stats.inserted / elapsed:,.0f} rows/s)"
                    )

    stats.seconds = time.perf_counter() - started
    return stats

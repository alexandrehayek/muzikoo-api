"""Database access.

Two doors into the same PostgreSQL database, on purpose:

* ``get_engine()`` — SQLAlchemy, used by the API and by ``pd.read_sql``.
* ``raw_connection()`` — plain psycopg2, used by the bulk loader where
  ``execute_values`` is a lot faster than going through the ORM layer.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

import psycopg2
from sqlalchemy import Engine, create_engine, text

from .config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
    )


@contextmanager
def raw_connection() -> Iterator[psycopg2.extensions.connection]:
    """psycopg2 connection with autocommit off, committed on clean exit."""
    conn = psycopg2.connect(**get_settings().psycopg2_dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_sql_file(path, *, connection=None) -> None:
    """Execute a .sql script. Split-free: psycopg2 handles multi-statement text."""
    sql = path.read_text(encoding="utf-8")
    if connection is not None:
        with connection.cursor() as cur:
            cur.execute(sql)
        return
    with raw_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)


def check_connection() -> str:
    """Return the server version, or raise with the underlying error."""
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT version()")).scalar_one()


def table_exists(name: str = "tracks") -> bool:
    with get_engine().connect() as conn:
        return bool(
            conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}).scalar()
        )


def count_tracks() -> int:
    with get_engine().connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM tracks")).scalar_one())

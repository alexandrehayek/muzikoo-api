"""Settings, read once from the environment.

Two deployment shapes:

* **development** — local PostgreSQL, credentials assembled from the DB_*
  variables in .env.
* **production** — a single DATABASE_URL (Supabase), injected by the host.
  Vercel has no .env file, so DATABASE_URL is the only thing required.

DATABASE_URL always wins when set, which is what lets the same code run in both
places without a branch at the call site.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# On Vercel there is no .env and variables come from the project settings;
# load_dotenv() on a missing file is a no-op rather than an error.
load_dotenv(PROJECT_ROOT / ".env")


def _path(value: str) -> Path:
    """Resolve a possibly-relative path against the project root."""
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


def normalise_database_url(url: str) -> str:
    """Make a hosted connection string usable by SQLAlchemy + psycopg2.

    Supabase (like Heroku) hands out URLs starting with ``postgres://``, a
    scheme SQLAlchemy refuses rather than guessing at. ``postgresql://`` works
    but leaves the driver implicit, so both are pinned to psycopg2 explicitly.
    """
    for already_explicit in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(already_explicit):
            return url
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix) :]
    return url


@dataclass(frozen=True)
class Settings:
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "muzikoo_api")
    db_user: str = os.getenv("DB_USER", "muzikoo")
    db_password: str = os.getenv("DB_PASSWORD", "")

    # Single connection string, used in production. Set by the host (Vercel ->
    # Supabase); when present it overrides every DB_* value above.
    database_url_env: str = os.getenv("DATABASE_URL", "")

    # Vercel sets VERCEL=1 in every build and runtime environment.
    is_serverless: bool = bool(os.getenv("VERCEL"))

    dataset_path: Path = field(default_factory=lambda: _path(os.getenv("DATASET_PATH", "data/dataset.json")))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "5000"))
    model_path: Path = field(default_factory=lambda: _path(os.getenv("MODEL_PATH", "models/knn_model.joblib")))

    default_limit: int = int(os.getenv("DEFAULT_LIMIT", "50"))
    max_limit: int = int(os.getenv("MAX_LIMIT", "200"))

    # Comma-separated list of accepted api_key values. Read through a factory
    # so tests can set API_KEYS before the first get_settings() call.
    api_keys: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()
        )
    )
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
        )
    )

    @property
    def is_production(self) -> bool:
        """Production means "driven by a DATABASE_URL", not "on Vercel".

        Keeping the two apart means `DATABASE_URL=... make api` reproduces the
        production database locally, which is how you verify a Supabase restore
        before pointing the deployment at it.
        """
        return bool(self.database_url_env)

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL. DATABASE_URL wins if it is set."""
        if self.database_url_env:
            return normalise_database_url(self.database_url_env)
        return (
            f"postgresql+psycopg2://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def psycopg2_dsn(self) -> dict[str, object]:
        """Keyword args for psycopg2.connect(), used by the bulk loader.

        With DATABASE_URL set, the whole URL is handed over as a DSN so the
        loader can target Supabase directly. The SQLAlchemy-only ``+psycopg2``
        suffix has to come back off first — libpq does not understand it.
        """
        if self.database_url_env:
            dsn = normalise_database_url(self.database_url_env)
            return {"dsn": dsn.replace("+psycopg2://", "://", 1)}
        return {
            "host": self.db_host,
            "port": self.db_port,
            "dbname": self.db_name,
            "user": self.db_user,
            "password": self.db_password,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

"""Settings, read once from the environment (.env supported)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _path(value: str) -> Path:
    """Resolve a possibly-relative path against the project root."""
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class Settings:
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "muzikoo_api")
    db_user: str = os.getenv("DB_USER", "muzikoo")
    db_password: str = os.getenv("DB_PASSWORD", "")

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
    def database_url(self) -> str:
        """SQLAlchemy URL. DATABASE_URL wins if it is set."""
        explicit = os.getenv("DATABASE_URL")
        if explicit:
            return explicit
        return (
            f"postgresql+psycopg2://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def psycopg2_dsn(self) -> dict[str, object]:
        """Keyword args for psycopg2.connect(), used by the bulk loader."""
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

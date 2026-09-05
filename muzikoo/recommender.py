"""KNN track recommender.

Design note — the model holds *only* the feature matrix and the track ids that
produced it. It never carries lyrics or titles. So:

* the artifact stays small enough to load at API startup;
* neighbour ids are resolved against the database at query time, meaning
  descriptive data is never stale relative to the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import MinMaxScaler
from sqlalchemy.engine import Engine

from .config import get_settings
from .db import get_engine
from . import repository

# Features used to fit the model, in this order.
FEATURE_COLUMNS: tuple[str, ...] = (
    "popularity",
    "energy",
    "danceability",
    "positiveness",
    "speechiness",
    "liveness",
    "acousticness",
    "instrumentalness",
    "explicit",
    "loudness",
    "tempo",
)


class ModelNotTrained(Exception):
    """No artifact on disk yet — run scripts/train_model.py."""


class TrackNotFound(Exception):
    """No row matches the given track/artist pair."""


@dataclass
class RecommenderArtifact:
    """What gets pickled: scaler + fitted index + id mapping + provenance."""

    scaler: MinMaxScaler
    model: NearestNeighbors
    track_ids: np.ndarray
    feature_columns: tuple[str, ...]
    n_rows: int
    trained_at: str
    sklearn_version: str

    @property
    def size(self) -> int:
        return int(self.n_rows)


# --------------------------------------------------------------------------
# fit
# --------------------------------------------------------------------------
def load_feature_frame(engine: Engine | None = None) -> pd.DataFrame:
    """Fetch id + the 11 feature columns for every usable track.

    `explicit` is cast to 0/1 in SQL so the frame is entirely numeric. Rows
    with a NULL in any feature are excluded, since they cannot be placed in the
    feature space (this dataset has none, but a partial re-load could).
    """
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in FEATURE_COLUMNS)
    sql = f"""
        SELECT id,
               popularity, energy, danceability, positiveness, speechiness,
               liveness, acousticness, instrumentalness,
               explicit::int AS explicit,
               loudness, tempo
        FROM tracks
        WHERE {not_null}
        ORDER BY id
    """
    return pd.read_sql(sql, engine or get_engine())


def fit(engine: Engine | None = None, *, n_neighbors: int = 5) -> RecommenderArtifact:
    """Fetch the frame, scale the features, fit the neighbour index."""
    df = load_feature_frame(engine)
    if df.empty:
        raise ValueError("no tracks with complete features — load the dataset first")

    X = df[list(FEATURE_COLUMNS)].astype("float64")

    # MinMax rather than StandardScaler: these features are bounded (0..100,
    # 0..1, 0/1), so mapping each to 0..1 puts them on equal footing and keeps
    # the boolean `explicit` comparable to the rest.
    scaler = MinMaxScaler().set_output(transform="pandas")
    X_scaled = scaler.fit_transform(X)

    model = NearestNeighbors(n_neighbors=min(n_neighbors, len(df)))
    model.fit(X_scaled)

    return RecommenderArtifact(
        scaler=scaler,
        model=model,
        track_ids=df["id"].to_numpy(dtype=np.int64),
        feature_columns=FEATURE_COLUMNS,
        n_rows=len(df),
        trained_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        sklearn_version=sklearn.__version__,
    )


# --------------------------------------------------------------------------
# save / load
# --------------------------------------------------------------------------
def save(artifact: RecommenderArtifact, path: Path | None = None) -> Path:
    path = Path(path or get_settings().model_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path, compress=3)
    return path


def load(path: Path | None = None) -> RecommenderArtifact:
    path = Path(path or get_settings().model_path)
    if not path.exists():
        raise ModelNotTrained(f"no model at {path} — run: python scripts/train_model.py")
    return joblib.load(path)


# --------------------------------------------------------------------------
# query
# --------------------------------------------------------------------------
def find_similar(
    artifact: RecommenderArtifact,
    track: str,
    artist: str,
    *,
    limit: int = 50,
    include_lyrics: bool = False,
    engine: Engine | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (query_track, neighbours) ordered by increasing distance.

    Asks for limit+1 neighbours and then drops the query track by id rather
    than blindly dropping the first result: with 498k tracks on a bounded
    feature space, exact feature ties are common, so the nearest neighbour at
    distance 0 is not guaranteed to be the query track itself.
    """
    query_track = repository.find_track(
        artist, track, include_lyrics=include_lyrics, engine=engine
    )
    if query_track is None:
        raise TrackNotFound(f"no track matching track={track!r} artist={artist!r}")

    missing = [c for c in FEATURE_COLUMNS if query_track.get(c) is None]
    if missing:
        raise TrackNotFound(
            f"track {track!r} by {artist!r} has no value for {', '.join(missing)}, "
            "so it cannot be placed in the feature space"
        )

    features = pd.DataFrame(
        [[float(query_track[c]) for c in FEATURE_COLUMNS]],
        columns=list(FEATURE_COLUMNS),
    )
    features_scaled = artifact.scaler.transform(features)

    k = min(limit + 1, artifact.n_rows)
    distances, indices = artifact.model.kneighbors(features_scaled, n_neighbors=k)

    neighbour_ids = artifact.track_ids[indices[0]]
    neighbour_distances = distances[0]

    keep = [
        (int(tid), float(dist))
        for tid, dist in zip(neighbour_ids, neighbour_distances)
        if int(tid) != int(query_track["id"])
    ][:limit]

    rows = repository.fetch_tracks_by_ids(
        [tid for tid, _ in keep], include_lyrics=include_lyrics, engine=engine
    )

    neighbours: list[dict[str, Any]] = []
    for tid, dist in keep:
        row = rows.get(tid)
        if row is None:  # deleted between fit and query
            continue
        neighbours.append({**row, "distance": round(dist, 10)})
    return query_track, neighbours

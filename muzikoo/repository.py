"""SQL queries behind the four API methods.

Every result set is paginated in SQL and carries its own total, obtained with
``count(*) OVER ()`` so that "one page of rows" and "how many rows in total"
cost a single round trip instead of two.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine, Row

from .db import get_engine
from .transform import BEST_FOR_LABELS, EMOTIONS

# Every column except lyrics, which can reach 80k chars per row and would
# dominate a 50-track response.
TRACK_FIELDS: tuple[str, ...] = (
    "id",
    "artist",
    "track",
    "duration",
    "emotion",
    "genre",
    "album",
    "release_date",
    "musical_key",
    "tempo",
    "loudness",
    "time_signature",
    "explicit",
    "popularity",
    "energy",
    "danceability",
    "positiveness",
    "speechiness",
    "liveness",
    "acousticness",
    "instrumentalness",
    "best_for",
)


def _select_list(include_lyrics: bool) -> str:
    fields = list(TRACK_FIELDS)
    if include_lyrics:
        fields.insert(3, "lyrics")
    return ", ".join(fields)


def _rows_to_dicts(rows: Sequence[Row]) -> list[dict[str, Any]]:
    """Row -> dict, dropping the window-function total."""
    return [{k: v for k, v in row._mapping.items() if k != "_total"} for row in rows]


def _total_of(rows: Sequence[Row]) -> int:
    return int(rows[0]._mapping["_total"]) if rows else 0


# The artist column holds a comma-separated list for collaborations
# ("Michael Jackson,Akon,Mark \"Exit\" Goodchild"), so an exact match on the
# whole string is not enough: also treat a match on any single member as a hit.
_ARTIST_MATCH = """
    (lower(artist) = lower(:artist)
     OR lower(:artist) = ANY(regexp_split_to_array(lower(artist), '\\s*,\\s*')))
"""


def find_track(
    artist: str,
    track: str,
    *,
    include_lyrics: bool = False,
    engine: Engine | None = None,
) -> dict[str, Any] | None:
    """Locate one track by exact (case-insensitive) name and artist.

    4,204 (artist, track) pairs appear more than once in the dataset, so the
    tie is broken deterministically on popularity, then id.
    """
    sql = text(
        f"""
        SELECT {_select_list(include_lyrics)}, count(*) OVER () AS _total
        FROM tracks
        WHERE lower(track) = lower(:track) AND {_ARTIST_MATCH}
        ORDER BY popularity DESC NULLS LAST, id
        LIMIT 1
        """
    )
    with (engine or get_engine()).connect() as conn:
        rows = conn.execute(sql, {"track": track.strip(), "artist": artist.strip()}).fetchall()
    found = _rows_to_dicts(rows)
    return found[0] if found else None


def fetch_tracks_by_ids(
    ids: Sequence[int],
    *,
    include_lyrics: bool = False,
    engine: Engine | None = None,
) -> dict[int, dict[str, Any]]:
    """Full rows for the ids the recommender returned, keyed by id.

    The KNN model only keeps numeric features in memory; the descriptive data
    is fetched here, so it is always current with the database.
    """
    if not ids:
        return {}
    sql = text(
        f"""
        SELECT {_select_list(include_lyrics)}, 0 AS _total
        FROM tracks
        WHERE id = ANY(:ids)
        """
    )
    with (engine or get_engine()).connect() as conn:
        rows = conn.execute(sql, {"ids": list(ids)}).fetchall()
    return {row["id"]: row for row in _rows_to_dicts(rows)}


def search_tracks(
    track: str,
    artist: str | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    include_lyrics: bool = False,
    engine: Engine | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Substring search on track name, optionally narrowed by artist.

    Ordered by popularity. Exact title matches are floated to the top so that
    searching "Yesterday" does not bury it under "Yesterday Once More".
    """
    clauses = ["track ILIKE :pattern"]
    params: dict[str, Any] = {
        "pattern": f"%{track.strip()}%",
        "exact": track.strip(),
        "limit": limit,
        "offset": offset,
    }
    if artist:
        clauses.append("(artist ILIKE :artist_pattern OR " + _ARTIST_MATCH.strip() + ")")
        params["artist_pattern"] = f"%{artist.strip()}%"
        params["artist"] = artist.strip()

    sql = text(
        f"""
        SELECT {_select_list(include_lyrics)}, count(*) OVER () AS _total
        FROM tracks
        WHERE {' AND '.join(clauses)}
        ORDER BY (lower(track) = lower(:exact)) DESC,
                 popularity DESC NULLS LAST,
                 id
        LIMIT :limit OFFSET :offset
        """
    )
    with (engine or get_engine()).connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return _rows_to_dicts(rows), _total_of(rows)


def tracks_by_emotion(
    emotion: str,
    *,
    limit: int = 50,
    offset: int = 0,
    include_lyrics: bool = False,
    engine: Engine | None = None,
) -> tuple[list[dict[str, Any]], int]:
    sql = text(
        f"""
        SELECT {_select_list(include_lyrics)}, count(*) OVER () AS _total
        FROM tracks
        WHERE lower(emotion) = lower(:emotion)
        ORDER BY popularity DESC NULLS LAST, id
        LIMIT :limit OFFSET :offset
        """
    )
    with (engine or get_engine()).connect() as conn:
        rows = conn.execute(
            sql, {"emotion": emotion.strip(), "limit": limit, "offset": offset}
        ).fetchall()
    return _rows_to_dicts(rows), _total_of(rows)


def tracks_best_for(
    action: str,
    *,
    limit: int = 50,
    offset: int = 0,
    include_lyrics: bool = False,
    engine: Engine | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Tracks whose best_for list contains `action`.

    Exact membership in the comma-separated list, not a LIKE on the string, so
    a label can never match a fragment of another one.
    """
    sql = text(
        f"""
        SELECT {_select_list(include_lyrics)}, count(*) OVER () AS _total
        FROM tracks
        WHERE lower(:action) = ANY(string_to_array(best_for, ','))
        ORDER BY popularity DESC NULLS LAST, id
        LIMIT :limit OFFSET :offset
        """
    )
    with (engine or get_engine()).connect() as conn:
        rows = conn.execute(
            sql, {"action": action.strip(), "limit": limit, "offset": offset}
        ).fetchall()
    return _rows_to_dicts(rows), _total_of(rows)


def valid_emotion(value: str) -> bool:
    return value.strip().lower() in EMOTIONS


def valid_action(value: str) -> bool:
    return value.strip().lower() in BEST_FOR_LABELS

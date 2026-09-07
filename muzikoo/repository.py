"""SQL queries behind the four API methods.

Every result set is paginated in SQL and carries its own total, so that "one
page of rows" and "how many rows in total" cost a single round trip instead of
two. Where the total comes from depends on how expensive it is to count:

* ``count(*) OVER ()`` for the substring searches, whose match sets are small
  and whose size is not known ahead of time;
* a lookup in ``track_facet_counts`` for the two fixed-vocabulary filters
  (emotion, best_for), whose match sets run to six figures. The window function
  has to consume every matching row before it can emit the first one, which
  cancels the LIMIT — see sql/002_indexes.sql.
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
    """Locate one track by substring match on the name, plus the artist.

    Matching runs in both directions, so a caller does not have to know the
    stored title exactly:

    * **direct** — the stored title contains the query
      ("yesterday" finds "Yesterday Once More");
    * **reverse** — the query contains the stored title
      ("Yesterday (Remastered 2009)" finds "Yesterday").

    Direct matches are preferred, then ordered shortest-first: any direct match
    is at least as long as the query, so the shortest one *is* the exact title
    when it exists. Reverse matches are ordered longest-first instead, since the
    longest stored title that still fits inside the query is the most specific.
    Popularity and id break the remaining ties — 4,204 (artist, track) pairs
    appear more than once, and equal titles have equal length, so without them
    the pick would not be stable.

    Run as two statements rather than one OR'd query, which is *the same
    result* — a direct match always outranks a reverse one, so if any direct
    match exists the combined query would pick from that group anyway — but
    far cheaper. `:track ILIKE CONCAT('%', track, '%')` puts the column on the
    pattern side, so it cannot use an index, and OR-ing it in makes PostgreSQL
    discard the trigram index for the whole predicate: 434 ms sequential scan
    over 492,976 rows, against 21 ms for the indexed direct match. The scan is
    now only paid on the rare query that has no direct match at all.
    """
    name = track.strip()
    params = {"track": name, "pattern": f"%{name}%", "artist": artist.strip()}

    with (engine or get_engine()).connect() as conn:
        for match, order in (
            # Shortest direct match first: any direct match is at least as long
            # as the query, so the shortest is the exact title when it exists.
            ("track ILIKE :pattern", "length(track) ASC"),
            # Longest reverse match first: the longest stored title that still
            # fits inside the query is the most specific one.
            (":track ILIKE CONCAT('%', track, '%')", "length(track) DESC"),
        ):
            rows = conn.execute(
                text(
                    f"""
                    SELECT {_select_list(include_lyrics)}
                    FROM tracks
                    WHERE {match} AND {_ARTIST_MATCH}
                    ORDER BY {order}, popularity DESC NULLS LAST, id
                    LIMIT 1
                    """
                ),
                params,
            ).fetchall()
            found = _rows_to_dicts(rows)
            if found:
                return found[0]
    return None


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
        SELECT {_select_list(include_lyrics)}
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


# Total for a fixed-vocabulary facet, as a scalar subquery so it travels with
# the page instead of costing a second round trip. COALESCE covers the case
# where the view has not been refreshed since a value first appeared.
_FACET_TOTAL = """
    COALESCE((SELECT n FROM track_facet_counts
               WHERE facet = :facet AND value = :facet_value), 0) AS _total
"""


def tracks_by_emotion(
    emotion: str,
    *,
    limit: int = 50,
    offset: int = 0,
    include_lyrics: bool = False,
    engine: Engine | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Tracks with the given emotion, most popular first.

    The ORDER BY is spelled to match tracks_emotion_pop_id_idx exactly, so the
    index supplies the ordering and the scan stops after `limit` rows. Changing
    the direction, the NULLS placement or the tie-breaker here without changing
    the index turns this back into a full-table sort.
    """
    value = emotion.strip()
    sql = text(
        f"""
        SELECT {_select_list(include_lyrics)}, {_FACET_TOTAL}
        FROM tracks
        WHERE lower(emotion) = lower(:emotion)
        ORDER BY popularity DESC NULLS LAST, id
        LIMIT :limit OFFSET :offset
        """
    )
    with (engine or get_engine()).connect() as conn:
        rows = conn.execute(
            sql,
            {
                "emotion": value,
                "facet": "emotion",
                "facet_value": value.lower(),
                "limit": limit,
                "offset": offset,
            },
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

    Phrased as `@> ARRAY[...]` rather than the equivalent
    `... = ANY(string_to_array(...))`, because only the array-containment
    operator is indexable: `= ANY(...)` is scalar-in-array, which no GIN
    operator class implements, so it silently sequential-scanned all 498k rows.
    """
    value = action.strip().lower()
    sql = text(
        f"""
        SELECT {_select_list(include_lyrics)}, {_FACET_TOTAL}
        FROM tracks
        WHERE string_to_array(best_for, ',') @> ARRAY[:action]
        ORDER BY popularity DESC NULLS LAST, id
        LIMIT :limit OFFSET :offset
        """
    )
    with (engine or get_engine()).connect() as conn:
        rows = conn.execute(
            sql,
            {
                "action": value,
                "facet": "best_for",
                "facet_value": value,
                "limit": limit,
                "offset": offset,
            },
        ).fetchall()
    return _rows_to_dicts(rows), _total_of(rows)


def valid_emotion(value: str) -> bool:
    return value.strip().lower() in EMOTIONS


def valid_action(value: str) -> bool:
    return value.strip().lower() in BEST_FOR_LABELS

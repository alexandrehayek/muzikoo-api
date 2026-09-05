"""Map one raw dataset record onto one ``tracks`` row.

Pure functions, no database — so they can be exercised against the dataset
without PostgreSQL running (``scripts/load_data.py --dry-run``).

Quirks of this dataset that the coercions below deliberately absorb:

* every numeric field arrives as a *string* ("Popularity": "40") except
  "Tempo" / "Loudness (db)", which are already floats normalised to 0..1;
* "Release Date" is JSON ``null`` for ~30% of rows;
* "Length" is always "MM:SS";
* "Explicit" is the string "Yes" / "No";
* "Time signature" is ``null`` on a handful of rows;
* "emotion" has a long tail of junk values ("pink", "True", "Love", ...) on
  ~26 rows out of 498k; they are lower-cased and stored as-is, and simply
  never match the six emotions the API accepts;
* "Album" is an empty string on 23 rows, "song" on 14.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

# Column order for INSERT. transform_record() returns a tuple in this order.
COLUMNS: tuple[str, ...] = (
    "artist",
    "track",
    "lyrics",
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

# Source flag -> best_for label, in the order the labels should appear.
# "exercice" is spelled as specified.
BEST_FOR_FLAGS: tuple[tuple[str, str], ...] = (
    ("Good for Party", "party"),
    ("Good for Work/Study", "work"),
    ("Good for Relaxation/Meditation", "relaxation"),
    ("Good for Exercise", "exercice"),
    ("Good for Running", "running"),
    ("Good for Yoga/Stretching", "yoga"),
    ("Good for Driving", "driving"),
    ("Good for Social Gatherings", "social"),
    ("Good for Morning Routine", "morning"),
)

BEST_FOR_LABELS: tuple[str, ...] = tuple(label for _, label in BEST_FOR_FLAGS)

EMOTIONS: tuple[str, ...] = ("sadness", "joy", "love", "anger", "fear", "surprise")


class SkipRecord(Exception):
    """Record cannot become a usable row (no artist or no track name)."""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _int(value: Any) -> int | None:
    """'40' -> 40. Tolerates floats-as-strings and returns None on garbage."""
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_duration(value: Any) -> int | None:
    """'03:47' -> 227 seconds. Also accepts 'H:MM:SS' and a bare integer."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    seconds = 0
    for n in numbers:  # sexagesimal, least significant last
        seconds = seconds * 60 + n
    return seconds


def parse_explicit(value: Any) -> bool | None:
    """'Yes'/'No' -> True/False."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("yes", "y", "true", "1"):
        return True
    if s in ("no", "n", "false", "0"):
        return False
    return None


def parse_release_date(value: Any) -> date | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_best_for(record: Mapping[str, Any]) -> str | None:
    """Join the labels whose "Good for ..." flag is 1.

    Up to four flags are set on a single row in this dataset, so the result is
    a comma-separated list ("party,social"). NULL when no flag is set.
    """
    labels = [label for source_key, label in BEST_FOR_FLAGS if _int(record.get(source_key)) == 1]
    return ",".join(labels) if labels else None


def transform_record(record: Mapping[str, Any]) -> tuple:
    """Raw JSON object -> tuple ordered like COLUMNS.

    Raises SkipRecord when artist or track is missing, since both are NOT NULL
    and a track with no name cannot be looked up through the API anyway.
    """
    artist = _text(record.get("Artist(s)"))
    track = _text(record.get("song"))
    if not artist or not track:
        raise SkipRecord("missing artist or track name")

    emotion = _text(record.get("emotion"))

    return (
        artist,
        track,
        _text(record.get("text")),
        parse_duration(record.get("Length")),
        emotion.lower() if emotion else None,
        _text(record.get("Genre")),
        _text(record.get("Album")),
        parse_release_date(record.get("Release Date")),
        _text(record.get("Key")),
        _float(record.get("Tempo")),
        _float(record.get("Loudness (db)")),
        _text(record.get("Time signature")),
        parse_explicit(record.get("Explicit")),
        _int(record.get("Popularity")),
        _int(record.get("Energy")),
        _int(record.get("Danceability")),
        _int(record.get("Positiveness")),
        _int(record.get("Speechiness")),
        _int(record.get("Liveness")),
        _int(record.get("Acousticness")),
        _int(record.get("Instrumentalness")),
        parse_best_for(record),
    )

"""Transformation tests — pure functions, no database needed."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from muzikoo.transform import (
    BEST_FOR_LABELS,
    COLUMNS,
    SkipRecord,
    parse_best_for,
    parse_duration,
    parse_explicit,
    parse_release_date,
    transform_record,
)

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "dataset-sample.json"


def test_columns_match_field_spec():
    assert COLUMNS == (
        "artist", "track", "lyrics", "duration", "emotion", "genre", "album",
        "release_date", "musical_key", "tempo", "loudness", "time_signature",
        "explicit", "popularity", "energy", "danceability", "positiveness",
        "speechiness", "liveness", "acousticness", "instrumentalness", "best_for",
    )


@pytest.mark.parametrize(
    "raw,expected",
    [("03:47", 227), ("00:30", 30), ("1:02:33", 3753), ("0:00", 0), (None, None), ("", None), ("bogus", None)],
)
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == expected


@pytest.mark.parametrize(
    "raw,expected", [("Yes", True), ("No", False), ("yes", True), (None, None), ("maybe", None)]
)
def test_parse_explicit(raw, expected):
    assert parse_explicit(raw) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [("2013-04-29", date(2013, 4, 29)), (None, None), ("", None), ("29/04/2013", None)],
)
def test_parse_release_date(raw, expected):
    assert parse_release_date(raw) == expected


def test_best_for_none_when_no_flag_set():
    assert parse_best_for({"Good for Party": 0, "Good for Running": 0}) is None


def test_best_for_single_flag():
    assert parse_best_for({"Good for Exercise": 1}) == "exercice"


def test_best_for_joins_multiple_flags_in_declared_order():
    record = {"Good for Social Gatherings": 1, "Good for Party": 1, "Good for Driving": 1}
    assert parse_best_for(record) == "party,driving,social"


def test_best_for_labels_are_the_nine_documented_values():
    assert BEST_FOR_LABELS == (
        "party", "work", "relaxation", "exercice", "running", "yoga", "driving", "social", "morning",
    )


def test_transform_record_maps_and_coerces_every_field():
    record = {
        "Artist(s)": "!!!", "song": "Even When the Waters Cold", "text": "lyrics here",
        "Length": "03:47", "emotion": "sadness", "Genre": "hip hop", "Album": "Thr!!!er",
        "Release Date": "2013-04-29", "Key": "D min", "Tempo": 0.4378698225,
        "Loudness (db)": 0.785065407, "Time signature": "4/4", "Explicit": "No",
        "Popularity": "40", "Energy": "83", "Danceability": "71", "Positiveness": "87",
        "Speechiness": "4", "Liveness": "16", "Acousticness": "11", "Instrumentalness": "0",
        "Good for Party": 1, "Good for Morning Routine": 1,
    }
    row = dict(zip(COLUMNS, transform_record(record)))

    assert row["artist"] == "!!!"
    assert row["track"] == "Even When the Waters Cold"
    assert row["duration"] == 227                      # "03:47" -> seconds
    assert row["musical_key"] == "D min"
    assert row["release_date"] == date(2013, 4, 29)
    assert row["explicit"] is False
    assert row["popularity"] == 40 and isinstance(row["popularity"], int)
    assert row["tempo"] == pytest.approx(0.4378698225)
    assert row["loudness"] == pytest.approx(0.785065407)
    assert row["best_for"] == "party,morning"


def test_transform_record_lowercases_emotion():
    base = {"Artist(s)": "a", "song": "t", "emotion": "Love"}
    row = dict(zip(COLUMNS, transform_record(base)))
    assert row["emotion"] == "love"


def test_transform_record_empties_become_null():
    base = {"Artist(s)": "a", "song": "t", "Album": "", "Time signature": None}
    row = dict(zip(COLUMNS, transform_record(base)))
    assert row["album"] is None
    assert row["time_signature"] is None


@pytest.mark.parametrize("record", [{"Artist(s)": "a", "song": ""}, {"Artist(s)": "", "song": "t"}, {}])
def test_transform_record_skips_rows_without_identity(record):
    with pytest.raises(SkipRecord):
        transform_record(record)


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample dataset not present")
def test_every_sample_record_transforms():
    count = 0
    with SAMPLE.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = transform_record(json.loads(line))
            assert len(row) == len(COLUMNS)
            count += 1
    assert count > 0

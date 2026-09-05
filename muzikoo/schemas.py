"""Response shapes for /v1.

The endpoint is a method dispatcher, so each method has its own envelope. Every
envelope repeats the query parameters that produced it (artist/track/emotion/
action) plus, where the method is paginated, the OpenSearch-style counters
startpage / totalresults / startindex / itemsperpage.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TrackOut(BaseModel):
    """One track. `lyrics` is only present when requested, `distance` only in
    track.getsimilar results."""

    model_config = ConfigDict(extra="ignore")

    id: int
    artist: str
    track: str
    duration: int | None = Field(default=None, description="Track length in seconds")
    emotion: str | None = None
    genre: str | None = None
    album: str | None = None
    release_date: date | None = None
    musical_key: str | None = None
    tempo: float | None = Field(default=None, description="Normalised 0..1")
    loudness: float | None = Field(default=None, description="Normalised 0..1")
    time_signature: str | None = None
    explicit: bool | None = None
    popularity: int | None = None
    energy: int | None = None
    danceability: int | None = None
    positiveness: int | None = None
    speechiness: int | None = None
    liveness: int | None = None
    acousticness: int | None = None
    instrumentalness: int | None = None
    best_for: str | None = Field(
        default=None, description="Comma-separated activity labels, e.g. 'party,social'"
    )
    lyrics: str | None = None
    distance: float | None = Field(
        default=None, description="KNN distance from the query track (track.getsimilar only)"
    )


class _Paged(BaseModel):
    startpage: int
    totalresults: int
    startindex: int
    itemsperpage: int


class SimilarTracksResponse(BaseModel):
    method: str = "track.getsimilar"
    artist: str
    track: str
    limit: int
    totalresults: int
    tracks: list[TrackOut]


class SearchResponse(_Paged):
    method: str = "track.search"
    track: str
    artist: str | None = None
    tracks: list[TrackOut]


class EmotionResponse(_Paged):
    method: str = "track.byemotion"
    emotion: str
    tracks: list[TrackOut]


class BestForResponse(_Paged):
    method: str = "track.bestfor"
    action: str
    tracks: list[TrackOut]


class ErrorResponse(BaseModel):
    """last.fm-style error body."""

    error: int
    message: str


def to_tracks(rows: list[dict[str, Any]]) -> list[TrackOut]:
    return [TrackOut.model_validate(row) for row in rows]

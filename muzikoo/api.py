"""FastAPI app exposing every method on a single endpoint: GET /v1?method=...

Every /v1 request needs a valid `api_key` query parameter; see muzikoo.security.

    /v1?api_key=KEY&method=track.getsimilar&track=Yesterday&artist=The Beatles&limit=20
    /v1?api_key=KEY&method=track.search&track=Yesterday&artist=The Beatles&page=2
    /v1?api_key=KEY&method=track.byemotion&emotion=joy&limit=10
    /v1?api_key=KEY&method=track.bestfor&action=party&page=3
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Lock
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import recommender, repository
from .config import get_settings
from .errors import (
    ApiError,
    ERR_INVALID_METHOD,
    ERR_INVALID_PARAM,
    ERR_NOT_FOUND,
    ERR_SERVICE_OFFLINE,
)
from .schemas import (
    BestForResponse,
    EmotionResponse,
    SearchResponse,
    SimilarTracksResponse,
    to_tracks,
)
from .security import verify_api_key
from .transform import BEST_FOR_LABELS, EMOTIONS

METHODS = ("track.getsimilar", "track.search", "track.byemotion", "track.bestfor")


# Loaded once per process rather than per request: the artifact carries a
# fitted neighbour index over ~498k tracks, so re-reading it per call would
# dominate the response time.
state: dict[str, Any] = {"artifact": None, "model_error": None}

# Sync endpoints run in a threadpool, so two concurrent first-requests could
# otherwise both pay the ~23 MB load.
_model_lock = Lock()


def load_model_once() -> Any | None:
    """Return the artifact, loading it on first use.

    Deliberately not lifespan-only: some ASGI hosts — Vercel's Python adapter
    among them — do not run lifespan events, which would leave the model
    permanently unloaded in production and every track.getsimilar answering
    503. Lifespan below still warms this up when the host does support it, so
    a local server pays the cost at boot rather than on the first request.
    """
    if state["artifact"] is None and state["model_error"] is None:
        with _model_lock:
            if state["artifact"] is None and state["model_error"] is None:
                try:
                    state["artifact"] = recommender.load()
                except Exception as exc:  # noqa: BLE001 — other methods must still serve
                    state["model_error"] = str(exc)
    return state["artifact"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model_once()
    yield
    # Reset rather than clear(), so the keys still exist if the app is reused.
    state.update(artifact=None, model_error=None)


settings = get_settings()

app = FastAPI(
    title="Muzikoo API",
    version="1.0.0",
    description=__doc__,
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET"],
        allow_headers=["*"],
    )


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message})


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _require(value: str | None, name: str, method: str) -> str:
    if value is None or not value.strip():
        raise ApiError(400, ERR_INVALID_PARAM, f"{method} requires the '{name}' parameter")
    return value.strip()


def _paging(limit: int | None, page: int | None) -> tuple[int, int, int]:
    """-> (limit, page, offset), clamped to sane bounds."""
    resolved_limit = settings.default_limit if limit is None else limit
    if resolved_limit < 1:
        raise ApiError(400, ERR_INVALID_PARAM, "'limit' must be >= 1")
    resolved_limit = min(resolved_limit, settings.max_limit)

    resolved_page = 1 if page is None else page
    if resolved_page < 1:
        raise ApiError(400, ERR_INVALID_PARAM, "'page' must be >= 1")

    return resolved_limit, resolved_page, (resolved_page - 1) * resolved_limit


def _artifact() -> recommender.RecommenderArtifact:
    artifact = load_model_once()
    if artifact is None:
        raise ApiError(
            503,
            ERR_SERVICE_OFFLINE,
            f"recommender model unavailable ({state.get('model_error')}). "
            "Run: python scripts/train_model.py",
        )
    return artifact


# --------------------------------------------------------------------------
# endpoint
# --------------------------------------------------------------------------
@app.get(
    "/v1",
    response_model=None,
    summary="Method dispatcher",
    dependencies=[Depends(verify_api_key)],
)
def v1(
    method: Annotated[str, Query(description=f"One of: {', '.join(METHODS)}")],
    track: Annotated[str | None, Query(description="Track name")] = None,
    artist: Annotated[str | None, Query(description="Artist name")] = None,
    emotion: Annotated[str | None, Query(description=f"One of: {', '.join(EMOTIONS)}")] = None,
    action: Annotated[str | None, Query(description=f"One of: {', '.join(BEST_FOR_LABELS)}")] = None,
    limit: Annotated[int | None, Query(description="Results per page (default 50)")] = None,
    page: Annotated[int | None, Query(description="Page number (default 1)")] = None,
    lyrics: Annotated[bool, Query(description="Include the lyrics field")] = False,
) -> dict[str, Any]:
    method = method.strip().lower()

    if method == "track.getsimilar":
        return _get_similar(track, artist, limit, lyrics)
    if method == "track.search":
        return _search(track, artist, limit, page, lyrics)
    if method == "track.byemotion":
        return _by_emotion(emotion, limit, page, lyrics)
    if method == "track.bestfor":
        return _best_for(action, limit, page, lyrics)

    raise ApiError(
        400, ERR_INVALID_METHOD, f"unknown method {method!r}. Valid methods: {', '.join(METHODS)}"
    )


def _get_similar(
    track: str | None, artist: str | None, limit: int | None, lyrics: bool
) -> dict[str, Any]:
    track_name = _require(track, "track", "track.getsimilar")
    artist_name = _require(artist, "artist", "track.getsimilar")
    resolved_limit, _, _ = _paging(limit, None)

    try:
        query_track, neighbours = recommender.find_similar(
            _artifact(),
            track=track_name,
            artist=artist_name,
            limit=resolved_limit,
            include_lyrics=lyrics,
        )
    except recommender.TrackNotFound as exc:
        raise ApiError(404, ERR_NOT_FOUND, str(exc)) from exc

    return SimilarTracksResponse(
        # Echo the names as stored, not as typed, so the caller sees which
        # track the recommendations were actually computed from.
        artist=query_track["artist"],
        track=query_track["track"],
        limit=resolved_limit,
        totalresults=len(neighbours),
        tracks=to_tracks(neighbours),
    ).model_dump(mode="json")


def _search(
    track: str | None, artist: str | None, limit: int | None, page: int | None, lyrics: bool
) -> dict[str, Any]:
    track_name = _require(track, "track", "track.search")
    artist_name = artist.strip() if artist and artist.strip() else None
    resolved_limit, resolved_page, offset = _paging(limit, page)

    rows, total = repository.search_tracks(
        track_name,
        artist_name,
        limit=resolved_limit,
        offset=offset,
        include_lyrics=lyrics,
    )
    return SearchResponse(
        track=track_name,
        artist=artist_name,
        startpage=resolved_page,
        totalresults=total,
        startindex=offset,
        itemsperpage=resolved_limit,
        tracks=to_tracks(rows),
    ).model_dump(mode="json")


def _by_emotion(
    emotion: str | None, limit: int | None, page: int | None, lyrics: bool
) -> dict[str, Any]:
    value = _require(emotion, "emotion", "track.byemotion").lower()
    if not repository.valid_emotion(value):
        raise ApiError(
            400, ERR_INVALID_PARAM, f"'emotion' must be one of: {', '.join(EMOTIONS)}"
        )
    resolved_limit, resolved_page, offset = _paging(limit, page)

    rows, total = repository.tracks_by_emotion(
        value, limit=resolved_limit, offset=offset, include_lyrics=lyrics
    )
    return EmotionResponse(
        emotion=value,
        startpage=resolved_page,
        totalresults=total,
        startindex=offset,
        itemsperpage=resolved_limit,
        tracks=to_tracks(rows),
    ).model_dump(mode="json")


def _best_for(
    action: str | None, limit: int | None, page: int | None, lyrics: bool
) -> dict[str, Any]:
    value = _require(action, "action", "track.bestfor").lower()
    if not repository.valid_action(value):
        raise ApiError(
            400, ERR_INVALID_PARAM, f"'action' must be one of: {', '.join(BEST_FOR_LABELS)}"
        )
    resolved_limit, resolved_page, offset = _paging(limit, page)

    rows, total = repository.tracks_best_for(
        value, limit=resolved_limit, offset=offset, include_lyrics=lyrics
    )
    return BestForResponse(
        action=value,
        startpage=resolved_page,
        totalresults=total,
        startindex=offset,
        itemsperpage=resolved_limit,
        tracks=to_tracks(rows),
    ).model_dump(mode="json")


@app.get("/health", summary="Liveness / readiness")
def health() -> dict[str, Any]:
    from .db import count_tracks

    artifact = load_model_once()
    try:
        tracks = count_tracks()
        database = "ok"
    except Exception as exc:  # noqa: BLE001
        tracks, database = None, f"error: {exc}"

    return {
        "status": "ok" if database == "ok" and artifact is not None else "degraded",
        "environment": "production" if settings.is_production else "development",
        "database": database,
        "tracks": tracks,
        # Count only — never the keys themselves. Unauthenticated on purpose,
        # so a monitor can reach it without holding a key.
        "api_keys_configured": len(settings.api_keys),
        "model": {
            "loaded": artifact is not None,
            "rows": artifact.n_rows if artifact else None,
            "trained_at": artifact.trained_at if artifact else None,
            "error": state.get("model_error"),
        },
    }

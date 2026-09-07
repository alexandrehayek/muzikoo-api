"""End-to-end tests against the real database and the trained model.

Skipped automatically when PostgreSQL is unreachable or the tracks table is
empty, so the suite stays runnable on a fresh checkout.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_API_KEY
from muzikoo import repository
from muzikoo.api import app
from muzikoo.transform import BEST_FOR_LABELS, EMOTIONS


def _row_count() -> int:
    from muzikoo.db import count_tracks

    try:
        return count_tracks()
    except Exception:
        return 0


ROWS = _row_count()
needs_db = pytest.mark.skipif(ROWS == 0, reason="database empty or unreachable")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.params = {"api_key": TEST_API_KEY}
        yield c


@pytest.fixture(scope="module")
def any_track():
    """A track that certainly exists, taken from the most popular rows."""
    rows, _ = repository.tracks_by_emotion("joy", limit=1)
    if not rows:
        pytest.skip("no rows")
    return rows[0]


# --------------------------------------------------------------------------
# repository
# --------------------------------------------------------------------------
@needs_db
def test_find_track_is_case_insensitive(any_track):
    found = repository.find_track(any_track["artist"].upper(), any_track["track"].lower())
    assert found is not None
    assert found["track"].lower() == any_track["track"].lower()


@needs_db
def test_find_track_direct_substring_match(any_track):
    """A prefix of the stored title still locates it."""
    title = any_track["track"]
    if len(title) < 6:
        pytest.skip("title too short to truncate meaningfully")
    fragment = title[:-2]
    found = repository.find_track(any_track["artist"], fragment)
    assert found is not None
    # The contract of a direct match: the stored title contains the query.
    assert fragment.lower() in found["track"].lower()


@needs_db
def test_find_track_reverse_match_when_query_is_longer(any_track):
    """A query carrying extra qualifiers still finds the plain title —
    "<title> (Remastered 2009)" must resolve to "<title>"."""
    decorated = f"{any_track['track']} (Remastered 2009)"
    found = repository.find_track(any_track["artist"], decorated)
    assert found is not None
    assert found["track"].lower() in decorated.lower()


@needs_db
def test_find_track_prefers_the_exact_title_over_a_longer_one():
    """Shortest-direct-match ordering means an exact title wins over titles
    that merely contain it."""
    rows, _ = repository.search_tracks("love", limit=200)
    exact = next(
        (
            r
            for r in rows
            if r["track"].strip().lower() == "love"
        ),
        None,
    )
    if exact is None:
        pytest.skip("no track titled exactly 'love' for these artists")
    found = repository.find_track(exact["artist"], "love")
    assert found["track"].strip().lower() == "love"


@needs_db
def test_find_track_is_deterministic_for_duplicated_pairs():
    """4,204 (artist, track) pairs repeat; the same query must always return
    the same row."""
    picks = {
        repository.find_track("Adele", "Rolling in the Deep")["id"] for _ in range(5)
    }
    assert len(picks) == 1


@needs_db
def test_find_track_still_returns_none_for_nonsense():
    assert repository.find_track("zzz-no-artist-zzz", "zzz-no-track-zzz") is None


@needs_db
def test_find_track_matches_one_member_of_a_collaboration():
    """artist holds "A,B,C" for collaborations; querying just "B" must match."""
    candidates, _ = repository.search_tracks("a", limit=200)
    collab = next((r for r in candidates if "," in r["artist"]), None)
    if collab is None:
        pytest.skip("no collaboration in this page")
    member = collab["artist"].split(",")[1].strip()
    found = repository.find_track(member, collab["track"])
    assert found is not None


@needs_db
def test_search_orders_by_popularity_and_reports_total():
    rows, total = repository.search_tracks("love", limit=25)
    assert 0 < len(rows) <= 25
    assert total >= len(rows)
    popularities = [r["popularity"] for r in rows if r["popularity"] is not None]
    assert popularities == sorted(popularities, reverse=True)


@needs_db
def test_search_pagination_does_not_overlap():
    page1, total = repository.search_tracks("love", limit=10, offset=0)
    page2, _ = repository.search_tracks("love", limit=10, offset=10)
    if total <= 10:
        pytest.skip("not enough results to paginate")
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})


@needs_db
@pytest.mark.parametrize("emotion", EMOTIONS)
def test_every_emotion_returns_only_that_emotion(emotion):
    rows, total = repository.tracks_by_emotion(emotion, limit=20)
    assert total > 0
    assert {r["emotion"] for r in rows} == {emotion}


@needs_db
@pytest.mark.parametrize("action", BEST_FOR_LABELS)
def test_every_action_returns_tracks_carrying_that_label(action):
    rows, total = repository.tracks_best_for(action, limit=20)
    assert total > 0, f"no tracks for best_for={action}"
    for row in rows:
        assert action in row["best_for"].split(",")


@needs_db
def test_best_for_matches_whole_labels_only():
    """'work' must not be matched by some other label containing it."""
    rows, _ = repository.tracks_best_for("work", limit=50)
    assert all("work" in r["best_for"].split(",") for r in rows)


@needs_db
def test_lyrics_excluded_by_default_included_on_request(any_track):
    without = repository.find_track(any_track["artist"], any_track["track"])
    assert "lyrics" not in without
    with_lyrics = repository.find_track(
        any_track["artist"], any_track["track"], include_lyrics=True
    )
    assert "lyrics" in with_lyrics


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@needs_db
def test_search_envelope_has_the_opensearch_fields(client, any_track):
    r = client.get(
        "/v1", params={"method": "track.search", "track": any_track["track"], "limit": 5, "page": 2}
    )
    assert r.status_code == 200
    body = r.json()
    for field in ("track", "artist", "startpage", "totalresults", "startindex", "itemsperpage"):
        assert field in body
    assert body["startpage"] == 2
    assert body["itemsperpage"] == 5
    assert body["startindex"] == 5
    assert len(body["tracks"]) <= 5


@needs_db
def test_byemotion_envelope(client):
    r = client.get("/v1", params={"method": "track.byemotion", "emotion": "joy", "limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["emotion"] == "joy"
    for field in ("startpage", "totalresults", "startindex", "itemsperpage"):
        assert field in body
    assert len(body["tracks"]) == 3


@needs_db
def test_bestfor_envelope(client):
    r = client.get("/v1", params={"method": "track.bestfor", "action": "party", "limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "party"
    for field in ("startpage", "totalresults", "startindex", "itemsperpage"):
        assert field in body


@needs_db
def test_limit_is_capped_at_max_limit(client):
    from muzikoo.config import get_settings

    r = client.get("/v1", params={"method": "track.byemotion", "emotion": "joy", "limit": 100_000})
    assert r.status_code == 200
    assert r.json()["itemsperpage"] == get_settings().max_limit


@needs_db
def test_search_unknown_track_is_an_empty_page_not_an_error(client):
    r = client.get("/v1", params={"method": "track.search", "track": "zzzzz-no-such-track-zzzzz"})
    assert r.status_code == 200
    assert r.json()["totalresults"] == 0
    assert r.json()["tracks"] == []


# --------------------------------------------------------------------------
# recommender
# --------------------------------------------------------------------------
def _model_loaded() -> bool:
    from muzikoo.api import state

    return state.get("artifact") is not None


@needs_db
def test_getsimilar_returns_the_requested_number_of_neighbours(client, any_track):
    if not _model_loaded():
        pytest.skip("model not trained")
    r = client.get(
        "/v1",
        params={
            "method": "track.getsimilar",
            "track": any_track["track"],
            "artist": any_track["artist"],
            "limit": 12,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["artist"] and body["track"]
    assert len(body["tracks"]) == 12


@needs_db
def test_getsimilar_excludes_the_query_track_and_sorts_by_distance(client, any_track):
    if not _model_loaded():
        pytest.skip("model not trained")
    r = client.get(
        "/v1",
        params={
            "method": "track.getsimilar",
            "track": any_track["track"],
            "artist": any_track["artist"],
            "limit": 20,
        },
    )
    body = r.json()
    ids = [t["id"] for t in body["tracks"]]
    assert any_track["id"] not in ids
    assert len(ids) == len(set(ids))
    distances = [t["distance"] for t in body["tracks"]]
    assert distances == sorted(distances)


@needs_db
def test_getsimilar_unknown_track_is_404(client):
    if not _model_loaded():
        pytest.skip("model not trained")
    r = client.get(
        "/v1",
        params={"method": "track.getsimilar", "track": "zzz-nope-zzz", "artist": "zzz-nope-zzz"},
    )
    assert r.status_code == 404
    assert r.json()["error"] == 6


@needs_db
def test_getsimilar_defaults_to_50(client, any_track):
    if not _model_loaded():
        pytest.skip("model not trained")
    r = client.get(
        "/v1",
        params={
            "method": "track.getsimilar",
            "track": any_track["track"],
            "artist": any_track["artist"],
        },
    )
    assert r.json()["limit"] == 50
    assert len(r.json()["tracks"]) == 50


# --------------------------------------------------------------------------
# query plans
# --------------------------------------------------------------------------
# byemotion and bestfor are the only two methods that filter on a
# low-cardinality column, so they are the only two where the planner has a
# credible sequential scan to fall back to — and it will, silently, if the
# index stops matching the query. That regression cost 703 ms and 612 MB of
# buffers per request while every functional test above still passed, so it is
# asserted on the plan rather than on the response.
def _plan_for(sql, params) -> str:
    from sqlalchemy import text

    from muzikoo.db import get_engine

    with get_engine().connect() as conn:
        rows = conn.execute(text(f"EXPLAIN {sql}"), params).fetchall()
    return "\n".join(r[0] for r in rows)


@needs_db
@pytest.mark.parametrize("emotion", EMOTIONS)
def test_byemotion_page_is_served_by_an_index_scan(emotion):
    plan = _plan_for(
        f"SELECT {repository._select_list(False)}, {repository._FACET_TOTAL} FROM tracks "
        "WHERE lower(emotion) = lower(:emotion) "
        "ORDER BY popularity DESC NULLS LAST, id LIMIT :limit OFFSET :offset",
        {"emotion": emotion, "facet": "emotion", "facet_value": emotion, "limit": 50, "offset": 0},
    )
    assert "Seq Scan on tracks" not in plan, plan
    assert "tracks_emotion_pop_id_idx" in plan, plan
    # The index supplies the ordering; a Sort node means it no longer matches.
    assert "Sort Key" not in plan, plan


@needs_db
@pytest.mark.parametrize("action", BEST_FOR_LABELS)
def test_bestfor_page_is_served_by_an_index_scan(action):
    plan = _plan_for(
        f"SELECT {repository._select_list(False)}, {repository._FACET_TOTAL} FROM tracks "
        "WHERE string_to_array(best_for, ',') @> ARRAY[:action] "
        "ORDER BY popularity DESC NULLS LAST, id LIMIT :limit OFFSET :offset",
        {"action": action, "facet": "best_for", "facet_value": action, "limit": 50, "offset": 0},
    )
    assert "Seq Scan on tracks" not in plan, plan
    assert f"tracks_best_for_{action}_pop_idx" in plan, plan
    assert "Sort Key" not in plan, plan


@needs_db
@pytest.mark.parametrize("method,param,values", [
    ("track.byemotion", "emotion", EMOTIONS),
    ("track.bestfor", "action", BEST_FOR_LABELS),
])
def test_facet_totals_match_a_live_count(client, method, param, values):
    """track_facet_counts must agree with the table it summarises.

    `totalresults` is read from a materialized view, so a stale view would be
    invisible in every other test: the rows would still be right.
    """
    from sqlalchemy import text

    from muzikoo.db import get_engine

    for value in values:
        reported = client.get("/v1", params={"method": method, param: value}).json()["totalresults"]
        column = "lower(emotion) = :v" if param == "emotion" else "string_to_array(best_for, ',') @> ARRAY[:v]"
        with get_engine().connect() as conn:
            actual = conn.execute(
                text(f"SELECT count(*) FROM tracks WHERE {column}"), {"v": value}
            ).scalar_one()
        assert reported == actual, f"{method}={value}"


@needs_db
@pytest.mark.parametrize("match,order", [
    ("track ILIKE :pattern", "length(track) ASC"),
    (":track ILIKE CONCAT('%', track, '%')", "length(track) DESC"),
])
def test_find_track_both_passes_are_narrowed_by_the_artist_index(match, order):
    """The reverse pass has an unindexable track predicate by design.

    `:track ILIKE CONCAT('%', track, '%')` puts the column on the pattern side,
    so nothing can index it — and nothing needs to, because _ARTIST_MATCH
    narrows the scan to one artist first and filtering those is free. That only
    holds while *every* branch of the artist OR is index-servable; when one
    branch was `= ANY(...)` instead of `@>`, PostgreSQL could use neither and
    the reverse pass became a 402 ms scan of all 498k rows.
    """
    name = "here comes - New Release"
    plan = _plan_for(
        f"SELECT {repository._select_list(False)} FROM tracks "
        f"WHERE {match} AND {repository._ARTIST_MATCH} "
        f"ORDER BY {order}, popularity DESC NULLS LAST, id LIMIT 1",
        {"track": name, "pattern": f"%{name}%", "artist": "inxs"},
    )
    assert "Seq Scan on tracks" not in plan, plan


@needs_db
def test_artist_match_uses_both_or_branches_under_a_bitmap_or():
    """Both branches indexable is the whole point — assert both are in the plan.

    A plan that used only one would still be correct and still be fast here;
    it would just mean the other branch had silently stopped being servable.
    """
    plan = _plan_for(
        f"SELECT id FROM tracks WHERE {repository._ARTIST_MATCH}", {"artist": "inxs"}
    )
    assert "tracks_artist_lower_idx" in plan, plan
    assert "tracks_artist_members_gin_idx" in plan, plan


@needs_db
def test_reverse_match_resolves_a_title_the_query_string_contains():
    """The behaviour the reverse pass exists for: stored title inside the query."""
    found = repository.find_track("inxs", "here comes - New Release")
    assert found is not None
    assert found["track"].lower() in "here comes - new release"

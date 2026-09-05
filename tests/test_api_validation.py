"""Parameter validation for /v1.

These cases are rejected before any query runs, so the suite needs neither
PostgreSQL nor a trained model.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_API_KEY
from muzikoo.api import app


@pytest.fixture(scope="module")
def client():
    """Client that carries a valid api_key on every request, so the tests below
    exercise parameter validation rather than authentication."""
    with TestClient(app) as c:
        c.params = {"api_key": TEST_API_KEY}
        yield c


@pytest.fixture(scope="module")
def anon_client():
    """Client with no credentials, for the authentication tests."""
    with TestClient(app) as c:
        yield c


def test_unknown_method_is_rejected(client):
    r = client.get("/v1", params={"method": "track.getlyrics"})
    assert r.status_code == 400
    assert r.json()["error"] == 3
    assert "unknown method" in r.json()["message"]


def test_missing_method_is_a_422(client):
    assert client.get("/v1").status_code == 422


@pytest.mark.parametrize(
    "params,missing",
    [
        ({"method": "track.getsimilar"}, "track"),
        ({"method": "track.getsimilar", "track": "Yesterday"}, "artist"),
        ({"method": "track.getsimilar", "artist": "The Beatles"}, "track"),
        ({"method": "track.search"}, "track"),
        ({"method": "track.byemotion"}, "emotion"),
        ({"method": "track.bestfor"}, "action"),
    ],
)
def test_required_parameters(client, params, missing):
    r = client.get("/v1", params=params)
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == 6
    assert f"'{missing}'" in body["message"]


def test_blank_required_parameter_is_rejected(client):
    r = client.get("/v1", params={"method": "track.search", "track": "   "})
    assert r.status_code == 400
    assert r.json()["error"] == 6


@pytest.mark.parametrize("emotion", ["happiness", "pink", "True", ""])
def test_invalid_emotion(client, emotion):
    r = client.get("/v1", params={"method": "track.byemotion", "emotion": emotion})
    assert r.status_code == 400
    assert r.json()["error"] == 6


@pytest.mark.parametrize("emotion", ["joy", "JOY", " sadness ", "love", "anger", "fear", "surprise"])
def test_valid_emotions_accepted_by_the_validator(emotion):
    """Checked at the validator, not over HTTP: reaching the query would need a
    database, and these cases are about the accepted vocabulary."""
    from muzikoo.repository import valid_emotion

    assert valid_emotion(emotion)


@pytest.mark.parametrize("action", ["exercise", "sleep", "studying"])
def test_invalid_action(client, action):
    r = client.get("/v1", params={"method": "track.bestfor", "action": action})
    assert r.status_code == 400
    assert r.json()["error"] == 6


@pytest.mark.parametrize(
    "action",
    ["party", "work", "relaxation", "exercice", "running", "yoga", "driving", "social", "MORNING"],
)
def test_valid_actions_accepted_by_the_validator(action):
    from muzikoo.repository import valid_action

    assert valid_action(action)


@pytest.mark.parametrize("bad", [{"limit": 0}, {"limit": -5}, {"page": 0}, {"page": -1}])
def test_out_of_range_paging(client, bad):
    r = client.get("/v1", params={"method": "track.byemotion", "emotion": "joy", **bad})
    assert r.status_code == 400
    assert r.json()["error"] == 6


def test_getsimilar_reports_missing_model_as_503(client):
    """With no artifact on disk the method must degrade cleanly, not 500."""
    from muzikoo.api import state

    if state.get("artifact") is not None:
        pytest.skip("a trained model is present")
    r = client.get("/v1", params={"method": "track.getsimilar", "track": "x", "artist": "y"})
    assert r.status_code == 503
    assert r.json()["error"] == 8

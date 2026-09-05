"""api_key authentication on /v1.

None of these reach a query, so no database or trained model is needed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_API_KEY
from muzikoo.api import app
from muzikoo.config import get_settings
from muzikoo.security import INVALID_API_KEY_MESSAGE, is_valid_api_key

# A request that is valid in every respect except the credentials.
GOOD_PARAMS = {"method": "track.byemotion", "emotion": "joy", "limit": 1}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_missing_api_key_is_rejected(client):
    r = client.get("/v1", params=GOOD_PARAMS)
    assert r.status_code == 403
    assert r.json() == {"error": 10, "message": INVALID_API_KEY_MESSAGE}


@pytest.mark.parametrize(
    "bad_key",
    [
        "",                       # present but empty
        "nope",
        "test-api-ke",            # one char short
        "test-api-key ",          # trailing space
        "TEST-API-KEY",           # wrong case
        "test-api-key,nope",      # the whole configured list, not one key
        "clé-invalide",           # non-ASCII must not blow up compare_digest
    ],
)
def test_invalid_api_key_is_rejected(client, bad_key):
    r = client.get("/v1", params={**GOOD_PARAMS, "api_key": bad_key})
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == 10
    assert body["message"] == INVALID_API_KEY_MESSAGE


def test_error_message_is_exactly_as_specified():
    assert INVALID_API_KEY_MESSAGE == "Invalid API key. You must be granted a valid key"


def test_api_key_is_checked_before_parameters(client):
    """An unauthenticated caller learns nothing about parameter validity."""
    r = client.get("/v1", params={"method": "track.nonsense"})
    assert r.status_code == 403
    assert r.json()["error"] == 10


def test_valid_api_key_passes_authentication(client):
    """403 must be gone; anything after that is the database's business."""
    r = client.get("/v1", params={**GOOD_PARAMS, "api_key": TEST_API_KEY})
    assert r.status_code != 403


def test_every_configured_key_is_accepted():
    for key in get_settings().api_keys:
        assert is_valid_api_key(key, get_settings().api_keys)


def test_unknown_key_fails_membership_test():
    assert not is_valid_api_key("nope", get_settings().api_keys)


def test_no_keys_configured_means_nothing_is_valid():
    assert not is_valid_api_key("anything", ())


def test_health_is_reachable_without_a_key(client):
    """Monitoring must not need credentials, and must not leak the keys."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["api_keys_configured"] == len(get_settings().api_keys)
    assert "test-api-key" not in r.text


def test_openapi_declares_the_api_key_security_scheme(client):
    """Declared via APIKeyQuery, so /docs offers an Authorize button."""
    spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert any(
        s.get("in") == "query" and s.get("name") == "api_key" and s.get("type") == "apiKey"
        for s in schemes.values()
    )
    assert spec["paths"]["/v1"]["get"]["security"]

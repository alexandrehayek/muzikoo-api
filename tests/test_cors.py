"""CORS behaviour. conftest leaves CORS_ORIGINS unset, so this covers the
"empty means *" default that lets a browser client work in production."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_API_KEY
from muzikoo.api import app

ORIGIN = "https://muzikoo.example.com"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_any_origin_is_allowed_by_default(client):
    r = client.get("/health", headers={"Origin": ORIGIN})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"


def test_preflight_for_v1_succeeds(client):
    """The OPTIONS request a browser sends ahead of a cross-origin GET."""
    r = client.options(
        "/v1",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 200
    assert "GET" in r.headers["access-control-allow-methods"]


def test_credentials_stay_disabled(client):
    """Wildcard origin + credentials is the one unsafe combination (and is
    rejected by browsers anyway), so the header must be absent."""
    r = client.get("/health", headers={"Origin": ORIGIN})
    assert "access-control-allow-credentials" not in r.headers


def test_error_responses_also_carry_the_header(client):
    """Without it the browser hides the body and the caller sees an opaque
    network error instead of the API's message."""
    r = client.get(
        "/v1",
        params={"method": "nope", "api_key": TEST_API_KEY},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 400
    assert r.headers["access-control-allow-origin"] == "*"

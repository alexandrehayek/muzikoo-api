"""API key authentication.

The key travels as a query parameter (``?api_key=...``), matching the last.fm
convention the rest of this API follows. Declaring it through
``APIKeyQuery`` — rather than reading a plain query argument — registers it as
a security scheme, so /docs gets an Authorize button and the OpenAPI document
marks the endpoint as protected.
"""

from __future__ import annotations

from hmac import compare_digest

from fastapi import Security
from fastapi.security import APIKeyQuery

from .config import get_settings
from .errors import ApiError, ERR_INVALID_API_KEY, ERR_SERVICE_OFFLINE

INVALID_API_KEY_MESSAGE = "Invalid API key. You must be granted a valid key"

# auto_error=False so that a missing key reaches verify_api_key() and comes
# back in the last.fm error shape, instead of FastAPI's own {"detail": ...}.
api_key_query = APIKeyQuery(
    name="api_key",
    auto_error=False,
    description="API key, passed as a query parameter",
)


def verify_api_key(api_key: str | None = Security(api_key_query)) -> str:
    """Dependency: accept the request only for a configured key.

    A missing key is treated exactly like a wrong one — same code, same
    message — so the response never reveals whether a given key exists.
    """
    settings = get_settings()

    if not settings.api_keys:
        raise ApiError(
            503,
            ERR_SERVICE_OFFLINE,
            "no API keys are configured on the server; set API_KEYS in .env",
        )

    if api_key is None or not is_valid_api_key(api_key, settings.api_keys):
        raise ApiError(403, ERR_INVALID_API_KEY, INVALID_API_KEY_MESSAGE)

    return api_key


def is_valid_api_key(candidate: str, valid_keys: tuple[str, ...]) -> bool:
    """Constant-time membership test.

    compare_digest avoids leaking how much of a key matched through response
    timing; the loop is not short-circuited for the same reason. Both sides are
    encoded first because compare_digest rejects str containing non-ASCII, and
    the candidate is arbitrary user input.
    """
    encoded = candidate.encode("utf-8")
    matched = False
    for key in valid_keys:
        if compare_digest(encoded, key.encode("utf-8")):
            matched = True
    return matched

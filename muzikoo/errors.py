"""API error type and last.fm-style error codes.

Kept in its own module so that both ``api`` and ``security`` can raise
``ApiError`` without importing each other.
"""

from __future__ import annotations

# last.fm-style error codes.
ERR_INVALID_METHOD = 3
ERR_INVALID_PARAM = 6
ERR_NOT_FOUND = 6
ERR_SERVICE_OFFLINE = 8
ERR_INVALID_API_KEY = 10


class ApiError(Exception):
    """Rendered by the app's exception handler as {"error": code, "message": ...}."""

    def __init__(self, status: int, code: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

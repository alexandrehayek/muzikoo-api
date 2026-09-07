"""Vercel entry point.

Vercel's Python runtime looks for a module-level ASGI application called
``app`` in the file a request is routed to, and serves it directly — there is
no uvicorn process in production, which is why uvicorn is a dev-only
dependency.

vercel.json rewrites every path here, so /v1 and /health keep the same URLs
they have locally.
"""

import sys
from pathlib import Path

# The function runs with this file's directory as the entry, so the repository
# root (holding the muzikoo package) has to be put on the path explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muzikoo.api import app  # noqa: E402

__all__ = ["app"]

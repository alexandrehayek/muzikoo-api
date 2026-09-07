"""Vercel entrypoint.

Vercel's Python runtime detects FastAPI from requirements.txt and then looks
for a top-level `app` in one of a fixed set of filenames — `app.py`, `index.py`,
`server.py`, `main.py`, `wsgi.py`, `asgi.py` — at the project root or in
`src/`/`app/`. It routes every request to that app, so no vercel.json rewrite
is involved and `/v1` and `/health` keep the URLs they have locally.

The real application lives in muzikoo/api.py; this only re-exports it under a
name the runtime looks for. (The alternative is `tool.vercel.entrypoint` in a
pyproject.toml, which would also move dependency resolution off
requirements.txt.)
"""

import sys
from pathlib import Path

# Vercel runs functions with the project base as the working directory, but do
# not rely on that for imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from muzikoo.api import app  # noqa: E402

__all__ = ["app"]

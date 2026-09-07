#!/usr/bin/env bash
# Copy the local tracks table to Supabase WITHOUT the lyrics column.
#
#   SUPABASE_DB_URL='postgresql://...' scripts/db_push_slim.sh
#
# Why this exists: lyrics are ~610 MB of the ~820 MB database, and the API
# omits them from responses unless a request asks for &lyrics=true. Dropping
# them brings the database to ~210 MB, which fits Supabase's 500 MB free tier.
# The lyrics COLUMN is still created — it is simply NULL everywhere, so
# &lyrics=true returns null instead of failing.
#
# This streams straight from one server to the other through COPY, so nothing
# large is written to disk. It uses the schema/index scripts rather than
# pg_dump, which cannot exclude a single column.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

TARGET="${SUPABASE_DB_URL:-${DATABASE_URL:-}}"
if [[ -z "$TARGET" ]]; then
    echo "set SUPABASE_DB_URL (or DATABASE_URL) to the target connection string" >&2
    exit 1
fi
if [[ "$TARGET" == *":6543"* ]]; then
    echo "use the session-mode connection string (port 5432), not the pooler" >&2
    exit 1
fi

if [[ ! -f .env ]]; then
    echo "no .env found — run 'make db-bootstrap' first" >&2
    exit 1
fi
set -a; . ./.env; set +a
LOCAL_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# Every column except lyrics, in table order.
COLS="id,artist,track,duration,emotion,genre,album,release_date,musical_key,\
time_signature,explicit,tempo,loudness,popularity,energy,danceability,\
positiveness,speechiness,liveness,acousticness,instrumentalness,best_for"

echo ">> enabling pg_trgm (needed by the search indexes)"
# Supabase's postgres role may create this itself; if not, enable it under
# Database -> Extensions in the dashboard.
psql "$TARGET" --quiet -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" \
  || echo "   warning: could not create pg_trgm — enable it in the dashboard, then re-run"

echo ">> creating the schema on the target"
DATABASE_URL="$TARGET" .venv/bin/python scripts/init_db.py --stage schema --drop

echo ">> streaming rows (no lyrics) local -> target"
psql "$LOCAL_URL" --quiet -c "\copy (SELECT ${COLS} FROM tracks ORDER BY id) TO STDOUT" \
  | psql "$TARGET" --quiet -c "\copy tracks (${COLS}) FROM STDIN"

# The copy carries explicit ids, which leaves the BIGSERIAL sequence at 1.
# Harmless for a read-only API, but wrong the moment anything inserts.
echo ">> syncing the id sequence"
psql "$TARGET" --quiet -c \
  "SELECT setval(pg_get_serial_sequence('tracks','id'), COALESCE(max(id), 1)) FROM tracks" >/dev/null

echo ">> building indexes on the target"
DATABASE_URL="$TARGET" .venv/bin/python scripts/init_db.py --stage indexes

echo ">> done"

#!/usr/bin/env bash
# Dump the local database to a custom-format archive.
#
#   scripts/db_dump.sh                       # -> dumps/muzikoo_api-YYYYMMDD.dump
#   scripts/db_dump.sh path/to/out.dump
#
# Custom format (-Fc) rather than plain SQL, so pg_restore can run in parallel
# (--jobs) and pick what to restore.
#
# Size note: the full database is ~820 MB, ~610 MB of which is lyrics. That is
# fine on a Supabase Pro plan (8 GB) but exceeds the free tier's 500 MB — see
# scripts/db_push_slim.sh for a lyrics-free transfer that fits.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f .env ]]; then
    echo "no .env found — run 'make db-bootstrap' first" >&2
    exit 1
fi
set -a; . ./.env; set +a

OUT="${1:-dumps/${DB_NAME}-$(date +%Y%m%d).dump}"
mkdir -p "$(dirname "$OUT")"

echo ">> dumping ${DB_NAME} -> ${OUT}"
# --no-owner / --no-privileges: locally these objects belong to the "muzikoo"
# role, which does not exist on Supabase. Without them the restore reports an
# error for every GRANT and "ALTER ... OWNER TO".
PGPASSWORD="$DB_PASSWORD" pg_dump \
    --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" --dbname="$DB_NAME" \
    --format=custom --compress=9 \
    --no-owner --no-privileges \
    --file="$OUT"

echo ">> done: $(du -h "$OUT" | cut -f1)"

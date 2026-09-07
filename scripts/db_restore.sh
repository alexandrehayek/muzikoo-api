#!/usr/bin/env bash
# Restore a dump into Supabase (or any remote PostgreSQL).
#
#   SUPABASE_DB_URL='postgresql://...' scripts/db_restore.sh dumps/muzikoo_api-20260907.dump
#
# Use the SESSION-mode connection string, not the transaction pooler:
# pg_restore needs session state (it sets search_path, disables triggers,
# creates indexes), which a transaction-mode pooler does not preserve between
# statements. Supabase's session pooler is port 5432 on the pooler host; the
# transaction pooler used by the deployed API is port 6543.

set -euo pipefail

DUMP="${1:-}"
if [[ -z "$DUMP" ]]; then
    echo "usage: scripts/db_restore.sh <dump file>" >&2
    exit 1
fi
if [[ ! -f "$DUMP" ]]; then
    echo "dump not found: $DUMP" >&2
    exit 1
fi

TARGET="${SUPABASE_DB_URL:-${DATABASE_URL:-}}"
if [[ -z "$TARGET" ]]; then
    echo "set SUPABASE_DB_URL (or DATABASE_URL) to the target connection string" >&2
    exit 1
fi

# Guard against the most likely mistake: pointing a restore at the pooler the
# runtime uses. It fails in confusing, partial ways rather than cleanly.
if [[ "$TARGET" == *":6543"* ]]; then
    echo "refusing to restore through port 6543 (transaction pooler)." >&2
    echo "use the session-mode connection string (port 5432) instead." >&2
    exit 1
fi

echo ">> restoring $DUMP -> ${TARGET%%\?*}"
echo ">> this moves ~800 MB over the network; expect it to take a while"

# --clean --if-exists makes re-runs idempotent: existing objects are dropped
# first instead of colliding. --jobs parallelises the slow part (index builds);
# it is incompatible with --single-transaction, so a failure part-way leaves
# the database partly restored and the command should simply be re-run.
pg_restore \
    --dbname="$TARGET" \
    --no-owner --no-privileges \
    --clean --if-exists \
    --jobs=4 \
    "$DUMP"

# Verify through the transaction pooler, not the session one used above: that
# is the connection the deployed API will actually use.
echo ">> restored. verify with:"
echo "   DATABASE_URL='<transaction pooler url, port 6543>' make prod-check"

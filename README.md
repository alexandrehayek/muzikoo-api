# Muzikoo API

Track database, KNN similarity recommender and JSON API over a 498k-track
dataset.

The base dataset used is available on Kaggle: [here](https://www.kaggle.com/datasets/devdope/900k-spotify/data)

To explore or download the data, visit the Kaggle page linked above.

```
muzikoo-api/
├── muzikoo/                  application package
│   ├── config.py             settings from .env
│   ├── db.py                 SQLAlchemy engine + raw psycopg2 connection
│   ├── transform.py          raw JSON record -> tracks row (pure functions)
│   ├── ingest.py             chunked streaming loader
│   ├── recommender.py        KNN: fit / save / load / find_similar
│   ├── repository.py         SQL behind the four API methods
│   ├── schemas.py            pydantic response models
│   ├── errors.py             ApiError + last.fm-style error codes
│   ├── security.py           api_key authentication (APIKeyQuery)
│   └── api.py                FastAPI app, GET /v1 dispatcher
├── api/index.py              Vercel entry point (exports the ASGI app)
├── vercel.json               routing + function config
├── scripts/
│   ├── bootstrap_db.sh       create role + database, write .env  (superuser, once)
│   ├── init_db.py            apply schema / indexes
│   ├── load_data.py          dataset.json -> PostgreSQL
│   ├── train_model.py        fit and save the model
│   ├── db_dump.sh            pg_dump the local database
│   ├── db_restore.sh         pg_restore into Supabase
│   ├── db_push_slim.sh       copy to Supabase without lyrics (fits free tier)
│   └── check_prod.py         verify a production database is serveable
├── sql/
│   ├── 000_bootstrap.sql     role + database + pg_trgm
│   ├── 001_schema.sql        tracks table
│   └── 002_indexes.sql       indexes (built after the load)
├── tests/                    unit (no DB) + integration (auto-skipped)
├── data/dataset.json         1.3 GB, JSON Lines, gitignored
└── models/knn_model.joblib   trained artifact, gitignored
```

## Setup

Local development, against local PostgreSQL. For production see
[Deployment](#deployment--vercel--supabase).

```bash
make install                                        # .venv + requirements-dev.txt
PGPASSWORD='<superuser password>' make db-bootstrap # role + database + .env
make db-init                                        # create the tracks table
make db-load                                        # stream ~500k rows in chunks
make db-index                                       # build indexes after the load
make train                                          # fit + save the KNN model
make api                                            # http://localhost:8000/v1
```

`db-bootstrap` runs `scripts/bootstrap_db.sh`, which creates the `muzikoo_api`
database owned by a `muzikoo` role, generates a random password for that role
**and a random API key**, then writes both to `.env` (mode 600). The superuser
password is only used for that one step — everything after it connects as
`muzikoo`. Re-running reuses the existing password and key, so credentials
already handed out keep working.

Override the names if you want different ones:

```bash
APP_DB=other_db APP_USER=other_role PGPASSWORD='...' make db-bootstrap
```

If you already have a `.env` from before API keys existed, add one:

```bash
echo "API_KEYS=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >> .env
```

## The data

`data/dataset.json` is JSON **Lines** despite the extension — one complete
object per line — so `load_data.py` reads it line by line and inserts in chunks
of 5,000. Peak memory is one chunk, not the file.

| source field | column | note |
|---|---|---|
| `Artist(s)` | `artist` | comma-separated list for collaborations |
| `song` | `track` | |
| `text` | `lyrics` | up to ~80k chars |
| `Length` | `duration` | `"03:47"` parsed to **227 seconds** |
| `Key` | `musical_key` | renamed; `key` is a SQL keyword |
| `Loudness (db)` | `loudness` | already normalised 0..1 |
| `Tempo` | `tempo` | already normalised 0..1 |
| `Explicit` | `explicit` | `"Yes"`/`"No"` -> boolean |
| `Release Date` | `release_date` | null on ~30% of rows |
| 9 × `Good for ...` | `best_for` | see below |
| `Similar Songs` | — | skipped |

`best_for` is a comma-separated list of the labels whose flag is `1`
(`party`, `work`, `relaxation`, `exercice`, `running`, `yoga`, `driving`,
`social`, `morning`). Up to four flags are set on one row, e.g.
`"party,social"`; `NULL` when none are (~64% of rows). Queries test whole-label
membership, so a label can never match a fragment of another.

What a full load reports, and why:

```
read 498,052 lines | inserted 498,037 | malformed 1 | skipped 14
```

* **malformed 1** — line 427,560 is corrupt in the source file: the `"Album"`
  key/value pair was replaced by a stray `f`, so the line is not valid JSON.
* **skipped 14** — rows with an empty `song`. `track` is `NOT NULL` and a
  nameless track cannot be looked up through the API.

## The model

Fitted on the 11 features, min-max scaled:

```
popularity energy danceability positiveness speechiness liveness
acousticness instrumentalness explicit loudness tempo
```

The artifact stores the scaler, the fitted `NearestNeighbors` index and the
track ids — **not** titles or lyrics. Neighbour ids are resolved against the
database at query time, so responses can never be stale relative to the table,
and the artifact stays small enough to load once at API startup.

```python
from muzikoo import recommender
artifact = recommender.fit()             # read from DB, scale, fit
recommender.save(artifact)               # models/knn_model.joblib
artifact = recommender.load()
query, neighbours = recommender.find_similar(
    artifact, track="Yesterday", artist="The Beatles", limit=50
)
```

`find_similar` asks for `limit + 1` neighbours and drops the query track **by
id**, rather than blindly dropping the first result: on a bounded feature space
with 498k tracks, exact feature ties are common, so the nearest neighbour at
distance 0 is not necessarily the query track itself.

## API

One endpoint, `GET /v1`, dispatching on `method`. Interactive docs at `/docs`.

### Authentication

Every `/v1` request requires a valid `api_key` query parameter:

| param | | |
|---|---|---|
| `api_key` | required | one of the keys listed in `API_KEYS` |

Accepted keys come from the comma-separated `API_KEYS` setting in `.env`.
Declared with `APIKeyQuery` from `fastapi.security`, so `/docs` gets an
**Authorize** button and the OpenAPI document marks `/v1` as protected.

A key that is missing, empty or unknown gets the same answer — `403` with
last.fm error code `10` — so the response never reveals whether a given key
exists:

```json
{ "error": 10, "message": "Invalid API key. You must be granted a valid key" }
```

The key is checked **before** any parameter validation, so an unauthenticated
caller learns nothing about the rest of the API. Keys are compared in constant
time (`hmac.compare_digest`). `GET /health` is deliberately left open so a
monitor can reach it without holding a key; it reports how many keys are
configured, never their values.

With no `API_KEYS` set at all, `/v1` answers `503` (error code `8`) rather than
letting requests through — an unconfigured server fails closed.

### `method=track.getsimilar`

| param | | |
|---|---|---|
| `track` | required | track name |
| `artist` | required | artist name |
| `limit` | optional | number of similar tracks, default 50 |
| `api_key` | required | your API key |

```
http://127.0.0.1:8000/v1?method=track.getsimilar&track=yesterday&artist=the%20beatles&lyrics=true&limit=200&api_key=YOUR_API_KEY
```

```bash
curl -G http://localhost:8000/v1 \
  --data-urlencode api_key="$API_KEY" \
  --data-urlencode method=track.getsimilar \
  --data-urlencode track="Yesterday" \
  --data-urlencode artist="The Beatles" \
  --data-urlencode limit=20
```

```json
{
  "method": "track.getsimilar",
  "artist": "The Beatles",
  "track": "Yesterday",
  "limit": 20,
  "totalresults": 20,
  "tracks": [{ "id": 1234, "artist": "...", "track": "...", "distance": 0.0271, "...": "..." }]
}
```

`artist` and `track` echo the values **as stored**, so the caller can see which
of the 4,204 duplicate (artist, track) pairs the recommendations came from —
ties are broken on popularity, then id.

### `method=track.search`

| param | | |
|---|---|---|
| `track` | required | track name, substring match |
| `artist` | optional | narrows the search |
| `limit` | optional | results per page, default 50 |
| `page` | optional | page number, default 1 |
| `api_key` | required | your API key |

```
http://127.0.0.1:8000/v1?method=track.search&track=yesterday&api_key=YOUR_API_KEY
```

Ordered by popularity, with exact title matches floated to the top so that
searching *Yesterday* does not bury it under *Yesterday Once More*. Returns
`track`, `artist`, `startpage`, `totalresults`, `startindex`, `itemsperpage`
alongside `tracks`.

### `method=track.byemotion`

| param | | |
|---|---|---|
| `emotion` | required | `sadness`, `joy`, `love`, `anger`, `fear`, `surprise` |
| `limit` | optional | default 50 |
| `page` | optional | default 1 |
| `api_key` | required | your API key |

```
http://127.0.0.1:8000/v1?method=track.byemotion&emotion=joy&api_key=YOUR_API_KEY
```

Returns `emotion`, `startpage`, `totalresults`, `startindex`, `itemsperpage`,
`tracks`, ordered by popularity.

### `method=track.bestfor`

| param | | |
|---|---|---|
| `action` | required | `party`, `work`, `relaxation`, `exercice`, `running`, `yoga`, `driving`, `social`, `morning` |
| `limit` | optional | default 50 |
| `page` | optional | default 1 |
| `api_key` | required | your API key |

```
http://127.0.0.1:8000/v1?method=track.bestfor&action=yoga&page=1&api_key=YOUR_API_KEY
```

Returns `action`, `startpage`, `totalresults`, `startindex`, `itemsperpage`,
`tracks`, ordered by popularity.

### Conventions

* `lyrics` is **omitted by default** on every method — at up to 80k chars per
  track it would dominate a 50-track response. Add `&lyrics=true` to include it.
* `limit` is capped at `MAX_LIMIT` (200) rather than rejected.
* Errors use last.fm-style bodies with a matching HTTP status:

  ```json
  { "error": 6, "message": "track.getsimilar requires the 'artist' parameter" }
  ```

  | code | meaning | HTTP |
  |---|---|---|
  | `3` | unknown method | 400 |
  | `6` | bad or missing parameter | 400 |
  | `6` | track not found | 404 |
  | `8` | model not trained / no API keys configured | 503 |
  | `10` | missing or invalid API key | 403 |
* `GET /health` reports database connectivity, row count, model state and the
  number of configured API keys. No key needed.

## Deployment — Vercel + Supabase

Local development keeps using the local PostgreSQL. Production is the same code
pointed at Supabase through a single `DATABASE_URL`, which overrides every
`DB_*` variable. Nothing branches on "am I on Vercel" except connection
pooling.

| | development | production |
|---|---|---|
| database | local PostgreSQL | Supabase |
| configured by | `DB_*` in `.env` | `DATABASE_URL` in Vercel env vars |
| server | uvicorn (`make api`) | Vercel's ASGI runtime |
| SQLAlchemy pool | 5 connections | `NullPool` |

### 0. Get the connection strings

Supabase dashboard → **Connect** button at the top of the project page. It
offers three strings that differ only in host, port and username:

| shown as | string | use it for |
|---|---|---|
| Direct connection | `postgresql://postgres:[PASSWORD]@db.<ref>.supabase.co:5432/postgres` | migrations, if you have IPv6 or the IPv4 add-on |
| **Session pooler** | `postgresql://postgres.<ref>:[PASSWORD]@aws-<region>.pooler.supabase.com:5432/postgres` | **`SUPABASE_DB_URL`** — the restore |
| Transaction pooler | `postgresql://postgres.<ref>:[PASSWORD]@aws-<region>.pooler.supabase.com:6543/postgres` | **`DATABASE_URL`** — the deployed API |

Three things to watch:

* **The pooler username is `postgres.<project-ref>`, not `postgres`.** Only the
  direct connection uses a bare `postgres`. Mixing them up gives an
  authentication failure that looks like a wrong password.
* **`[YOUR-PASSWORD]` is a placeholder** — the dashboard never shows the
  password again. If you do not have it: Project Settings → Database → Reset
  database password.
* **Percent-encode special characters in the password.** It sits in the
  userinfo part of a URL, so `@ : / ? # [ ] %` break parsing.
  `python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" '<password>'`
  gives the safe form. Wrap the whole URL in single quotes so the shell does
  not eat `$` or `!` either.

Before restoring, enable the extension the search indexes need — Database →
Extensions → search `pg_trgm` → enable — then check the string works:

```bash
export SUPABASE_DB_URL='postgresql://postgres.<ref>:<password>@aws-<region>.pooler.supabase.com:5432/postgres'
psql "$SUPABASE_DB_URL" -c 'select version()'
```

### 1. Move the database to Supabase

The dump is taken locally and restored onto Supabase.

```bash
# 1. dump the local database (821 MB stored -> 276 MB archive, ~40 s)
make db-dump                                    # -> dumps/muzikoo_api-YYYYMMDD.dump

# 2. restore onto Supabase, using the SESSION pooler (port 5432)
export SUPABASE_DB_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres'
make db-restore DUMP=dumps/muzikoo_api-YYYYMMDD.dump
```

The raw commands, if you would rather not go through `make`:

```bash
pg_dump --host=localhost --port=5432 --username=muzikoo --dbname=muzikoo_api \
        --format=custom --compress=9 --no-owner --no-privileges \
        --file=dumps/muzikoo_api.dump

pg_restore --dbname="$SUPABASE_DB_URL" \
           --no-owner --no-privileges --clean --if-exists --jobs=4 \
           dumps/muzikoo_api.dump
```

Four flags carry the weight here:

* `--format=custom` — needed for `pg_restore --jobs`, which parallelises the
  index builds (the slow part of an 820 MB restore).
* `--no-owner --no-privileges` — locally every object belongs to the `muzikoo`
  role, which does not exist on Supabase. Without these, the restore reports an
  error for every `GRANT` and `ALTER ... OWNER TO`.
* `--clean --if-exists` — makes re-runs idempotent instead of colliding with
  the objects left by the previous attempt.

Two things that will bite otherwise:

* **Restore through the session pooler (5432), not the transaction pooler
  (6543).** `pg_restore` relies on session state — `search_path`, deferred
  triggers, index creation — which a transaction-mode pooler does not carry
  between statements, so the restore fails in partial, confusing ways.
  `scripts/db_restore.sh` refuses a `:6543` URL for this reason.
* **Enable `pg_trgm` first** (Supabase dashboard → Database → Extensions). The
  dump contains `CREATE EXTENSION pg_trgm`, but it is created by the superuser
  locally and may not restore cleanly. Without it the two GIN indexes are
  missing and `track.search` degrades to sequential scans.

Then confirm the result before pointing the deployment at it:

```bash
DATABASE_URL="$SUPABASE_DB_URL" make prod-check
```

That reports row count, table size, `pg_trgm`, all six expected indexes, and
runs one query of each API shape.

#### Size: 820 MB, of which 610 MB is lyrics

The full database does not fit Supabase's **500 MB free tier** — it needs a paid
plan (Pro is 8 GB). If you want it on the free tier, transfer without lyrics:

```bash
SUPABASE_DB_URL='postgresql://...:5432/postgres' make db-push-slim
```

That streams every column except `lyrics` straight from one server to the
other, landing at roughly 210 MB. The `lyrics` column still exists and is
`NULL`, so `&lyrics=true` returns `null` rather than failing — and since the
API omits lyrics from responses by default, nothing else changes. `pg_dump`
cannot exclude a single column, which is why this path uses `COPY` and the
schema scripts rather than a dump.

### 2. Deploy to Vercel

```bash
npm i -g vercel
vercel link
vercel env add DATABASE_URL production     # the TRANSACTION pooler URL, port 6543
vercel env add API_KEYS production
vercel --prod
```

Then:

```bash
curl "https://<your-deployment>.vercel.app/health"
curl "https://<your-deployment>.vercel.app/v1?method=track.byemotion&emotion=joy&api_key=YOUR_API_KEY"
```

What makes it work:

* **`api/index.py`** exports the ASGI `app`; `vercel.json` rewrites every path
  to it, so `/v1` and `/health` keep the URLs they have locally.
* **`DATABASE_URL` must be the transaction pooler (port 6543).** A serverless
  invocation is frozen between requests, so a pooled connection is unusable
  next time while still holding a slot on Supabase. The engine switches to
  `NullPool` when `VERCEL` is set, and forces `sslmode=require` in production.
* **`postgres://` is normalised.** Supabase hands out that scheme; SQLAlchemy
  rejects it. `normalise_database_url` rewrites it to
  `postgresql+psycopg2://`.
* **The model is committed** (`models/knn_model.joblib`, ~23 MB) and pulled in
  by `includeFiles`, because Vercel builds from the git repository. It is the
  one artifact that has to ship; retraining means committing a new one.
* **The model loads lazily,** not only in `lifespan`. Vercel's Python adapter
  does not reliably run ASGI lifespan events, which would otherwise leave
  `track.getsimilar` answering 503 forever in production.
* **`requirements.txt` is the production set;** `requirements-dev.txt` adds
  uvicorn and pytest. Vercel installs the former, so the bundle does not carry
  a server it never runs.
* **`.vercelignore` excludes `data/`** — the 1.3 GB dataset is only needed by
  the loader, which runs locally.

The function bundle is ~425 MB: scipy, pandas, numpy and scikit-learn dominate
it, and scikit-learn is required at request time, not just for training —
unpickling the artifact reconstructs `MinMaxScaler` and `NearestNeighbors`.
That fits Vercel's 500 MB Python limit, but not by a wide margin; if it ever
stops fitting, dropping pandas from the inference path is the first move (only
`recommender.fit` genuinely needs it).

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Unit tests cover the transformations, api_key authentication and every
parameter-validation path with no database. The integration suite exercises the
real queries and the model, and skips itself when PostgreSQL is unreachable or
the table is empty.

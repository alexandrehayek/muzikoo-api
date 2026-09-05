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
├── scripts/
│   ├── bootstrap_db.sh       create role + database, write .env  (superuser, once)
│   ├── init_db.py            apply schema / indexes
│   ├── load_data.py          dataset.json -> PostgreSQL
│   └── train_model.py        fit and save the model
├── sql/
│   ├── 000_bootstrap.sql     role + database + pg_trgm
│   ├── 001_schema.sql        tracks table
│   └── 002_indexes.sql       indexes (built after the load)
├── tests/                    unit (no DB) + integration (auto-skipped)
├── data/dataset.json         1.3 GB, JSON Lines, gitignored
└── models/knn_model.joblib   trained artifact, gitignored
```

## Setup

```bash
make install                                        # .venv + dependencies
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

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Unit tests cover the transformations, api_key authentication and every
parameter-validation path with no database. The integration suite exercises the
real queries and the model, and skips itself when PostgreSQL is unreachable or
the table is empty.

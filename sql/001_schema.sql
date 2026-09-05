-- Table only. Indexes live in 002_indexes.sql and are created *after* the bulk
-- load: maintaining 6 indexes while inserting ~500k rows is markedly slower
-- than building them once at the end.

CREATE TABLE IF NOT EXISTS tracks (
    id                BIGSERIAL PRIMARY KEY,

    -- identity
    artist            TEXT             NOT NULL,   -- source "Artist(s)", may be a comma-separated list
    track             TEXT             NOT NULL,   -- source "song"
    lyrics            TEXT,                        -- source "text", up to ~80k chars
    duration          INTEGER,                     -- source "Length" ("MM:SS"), stored as seconds

    -- descriptive
    emotion           TEXT,                        -- sadness | joy | love | anger | fear | surprise
    genre             TEXT,
    album             TEXT,
    release_date      DATE,                        -- ~30% of rows are null in the dataset
    musical_key       TEXT,                        -- source "Key", e.g. "D min"
    time_signature    TEXT,                        -- e.g. "4/4"
    explicit          BOOLEAN,                     -- source "Explicit" = "Yes"/"No"

    -- audio features: tempo and loudness arrive pre-normalised to 0..1,
    -- everything else is an integer percentage 0..100
    tempo             DOUBLE PRECISION,
    loudness          DOUBLE PRECISION,            -- source "Loudness (db)"
    popularity        SMALLINT,
    energy            SMALLINT,
    danceability      SMALLINT,
    positiveness      SMALLINT,
    speechiness       SMALLINT,
    liveness          SMALLINT,
    acousticness      SMALLINT,
    instrumentalness  SMALLINT,

    -- comma-separated activity labels derived from the nine "Good for ..."
    -- flags, e.g. "party,social". NULL when no flag is set (~64% of rows).
    best_for          TEXT
);

COMMENT ON COLUMN tracks.duration IS 'Track length in seconds (source "Length" was "MM:SS")';
COMMENT ON COLUMN tracks.best_for IS
    'Comma-separated subset of: party,work,relaxation,exercice,running,yoga,driving,social,morning';

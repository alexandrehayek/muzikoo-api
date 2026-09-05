-- Run AFTER scripts/load_data.py has finished.

-- track.getsimilar / exact lookups: lower(track) is the selective side, the
-- artist predicate then filters the handful of rows that come back.
CREATE INDEX IF NOT EXISTS tracks_track_lower_idx  ON tracks (lower(track));
CREATE INDEX IF NOT EXISTS tracks_artist_lower_idx ON tracks (lower(artist));

-- track.search does substring matching (ILIKE '%...%'), which no btree index
-- can serve. Trigram GIN indexes turn those full scans into index scans.
CREATE INDEX IF NOT EXISTS tracks_track_trgm_idx  ON tracks USING GIN (track  gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tracks_artist_trgm_idx ON tracks USING GIN (artist gin_trgm_ops);

-- track.byemotion: filter on emotion, order by popularity.
CREATE INDEX IF NOT EXISTS tracks_emotion_popularity_idx
    ON tracks (lower(emotion), popularity DESC);

-- track.bestfor: exact membership in the comma-separated label list.
CREATE INDEX IF NOT EXISTS tracks_best_for_gin_idx
    ON tracks USING GIN (string_to_array(best_for, ','));

ANALYZE tracks;

-- Run AFTER scripts/load_data.py has finished.

-- scripts/init_db.py applies this whole file as a single transaction, and
-- Supabase ships a 2 minute statement_timeout on the default role. Building
-- twelve indexes plus refreshing the facet view over ~498k rows lands close
-- enough to that ceiling that a cold instance can trip it and roll the entire
-- file back. Raised for the duration of this transaction only; it does not
-- affect the API's own sessions.
SET statement_timeout = '30min';

-- track.getsimilar / exact lookups: lower(track) is the selective side, the
-- artist predicate then filters the handful of rows that come back.
CREATE INDEX IF NOT EXISTS tracks_track_lower_idx  ON tracks (lower(track));
CREATE INDEX IF NOT EXISTS tracks_artist_lower_idx ON tracks (lower(artist));

-- The artist column holds a comma-separated list for collaborations, so the
-- artist predicate is an OR: match the whole string, or match one member.
-- PostgreSQL can only use indexes across an OR when *every* branch is
-- indexable (it combines them under BitmapOr) — one unindexable branch forces
-- a sequential scan for the whole predicate. The member branch used to be
-- `= ANY(regexp_split_to_array(...))`, which is scalar-in-array and therefore
-- unindexable, so it poisoned the OR and no artist lookup could use an index.
-- Rewritten as `@> ARRAY[...]` (see _ARTIST_MATCH in muzikoo/repository.py),
-- it is servable by this index and the OR becomes a BitmapOr of two.
--
-- This is what made the track.getsimilar reverse match expensive: that branch
-- is genuinely unindexable (`:track ILIKE CONCAT('%', track, '%')` puts the
-- column on the pattern side), but it never needed an index — one artist is a
-- few hundred rows, and filtering those is free. It only cost 402 ms because
-- the artist predicate could not narrow the scan first.
CREATE INDEX IF NOT EXISTS tracks_artist_members_gin_idx
    ON tracks USING GIN (regexp_split_to_array(lower(artist), '\s*,\s*'));

-- track.search does substring matching (ILIKE '%...%'), which no btree index
-- can serve. Trigram GIN indexes turn those full scans into index scans.
CREATE INDEX IF NOT EXISTS tracks_track_trgm_idx  ON tracks USING GIN (track  gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tracks_artist_trgm_idx ON tracks USING GIN (artist gin_trgm_ops);

-- track.byemotion: filter on emotion, order by popularity.
--
-- The column list has to match the ORDER BY *exactly* — same direction, same
-- NULLS placement, same tie-breaker — or PostgreSQL cannot use the index to
-- satisfy the ordering and falls back to sorting the whole match set. The
-- previous version of this index was (lower(emotion), popularity DESC), i.e.
-- NULLS FIRST by default and no `id`, against an ORDER BY of
-- `popularity DESC NULLS LAST, id`: close enough to look right, different
-- enough to be unusable. emotion=joy is 38% of the table, so the planner chose
-- a 498k-row sequential scan — 703 ms and 612 MB of buffers for 50 rows.
-- With the ordering matched it is a 45-buffer index scan that stops at 50.
DROP INDEX IF EXISTS tracks_emotion_popularity_idx;
CREATE INDEX IF NOT EXISTS tracks_emotion_pop_id_idx
    ON tracks (lower(emotion), popularity DESC NULLS LAST, id);

-- track.bestfor: exact membership in the comma-separated label list.
--
-- Kept as the fallback for any label that has no dedicated index below.
CREATE INDEX IF NOT EXISTS tracks_best_for_gin_idx
    ON tracks USING GIN (string_to_array(best_for, ','));

-- ... but GIN cannot return rows *in order*, so on its own it still means
-- "fetch all 10,973 yoga tracks from 8,505 scattered heap blocks, sort, keep
-- 50". One partial btree per label carries the popularity ordering inside the
-- index, which turns that back into a 38-buffer scan of exactly 50 rows.
--
-- Nine labels, ~273k index entries in total (~6 MB): every label is small
-- relative to the table, so the partial indexes together cost far less than
-- one full-table index would.
--
-- Written as a loop because the label list is fixed and closed — it mirrors
-- BEST_FOR_LABELS in muzikoo/transform.py. A label added there needs its index
-- added here too; until then it is served, more slowly, by the GIN index above.
DO $$
DECLARE
    label text;
BEGIN
    FOREACH label IN ARRAY ARRAY[
        'party', 'work', 'relaxation', 'exercice', 'running',
        'yoga', 'driving', 'social', 'morning'
    ]
    LOOP
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON tracks (popularity DESC NULLS LAST, id) '
            'WHERE string_to_array(best_for, '','') @> ARRAY[%L]',
            format('tracks_best_for_%s_pop_idx', label),
            label
        );
    END LOOP;
END $$;

-- Row counts per facet, for the `totalresults` field.
--
-- Counting the match set at request time is what the page query used to do, via
-- `count(*) OVER ()`. That window function has to consume *every* matching row
-- before it can emit the first one, so it defeats the LIMIT: byemotion=joy
-- buffered all 189,366 matches (spilling 28 MB to temp files) to return 50.
-- Even reduced to a bare `count(*)` on the right index it is ~200 ms, because a
-- 38%-of-the-table facet is genuinely expensive to count.
--
-- The dataset is bulk-loaded and then static, so the exact counts can simply be
-- computed once here: 15 rows, read back as a scalar subquery in the same
-- statement as the page, which keeps it at one round trip. Re-running this file
-- refreshes it; so does `REFRESH MATERIALIZED VIEW track_facet_counts` after
-- any change to tracks.
CREATE MATERIALIZED VIEW IF NOT EXISTS track_facet_counts AS
    SELECT 'emotion' AS facet, lower(emotion) AS value, count(*) AS n
      FROM tracks
     WHERE emotion IS NOT NULL
     GROUP BY 2
    UNION ALL
    SELECT 'best_for' AS facet, label AS value, count(*) AS n
      FROM tracks, unnest(string_to_array(best_for, ',')) AS label
     WHERE best_for IS NOT NULL
     GROUP BY 2;

CREATE UNIQUE INDEX IF NOT EXISTS track_facet_counts_key_idx
    ON track_facet_counts (facet, value);

REFRESH MATERIALIZED VIEW track_facet_counts;

ANALYZE tracks;
ANALYZE track_facet_counts;

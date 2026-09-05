-- Creates the application role and database. Run ONCE as a superuser.
--
--   scripts/bootstrap_db.sh                 (wrapper, recommended)
--
-- or by hand, connected to the "postgres" database:
--
--   psql -h localhost -U postgres -d postgres -v ON_ERROR_STOP=1 \
--        -v app_user=muzikoo -v app_password=secret -v app_db=muzikoo_api \
--        -f sql/000_bootstrap.sql
--
-- Everything is driven through \gexec rather than a DO $$...$$ block on
-- purpose: psql does not substitute :'variables' inside dollar-quoted strings,
-- so a DO block would receive the literal text ":'app_user'".

\set ON_ERROR_STOP on

-- Role: create when missing, then (re)set login + password either way.
SELECT format('CREATE ROLE %I', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
\gexec

-- Database: CREATE DATABASE cannot run inside a transaction or a DO block.
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db')
\gexec

-- pg_trgm powers the substring search in track.search. It is not a "trusted"
-- extension in PG 16, so only a superuser can install it — hence doing it here
-- rather than in 001_schema.sql, which runs as the application role.
\connect :app_db
CREATE EXTENSION IF NOT EXISTS pg_trgm;
GRANT ALL ON SCHEMA public TO :"app_user";

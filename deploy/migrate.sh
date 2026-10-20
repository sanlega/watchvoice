#!/bin/sh
# Applies 001 (v0.2 baseline) then backend/migrations/NNN_*.sql (not *.down.sql) exactly once each.
set -eu
export PGPASSWORD="$(cat "$PGPASSWORD_FILE")"
psql -v ON_ERROR_STOP=1 -qc "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
for f in /migrations/001_schema.sql $(ls /migrations/up/*.sql | grep -v '\.down\.sql$' | sort); do
  name=$(basename "$f")
  if [ "$(psql -tAc "SELECT 1 FROM schema_migrations WHERE name='$name'")" = "1" ]; then continue; fi
  echo "applying $name"
  psql -v ON_ERROR_STOP=1 -q -f "$f"
  psql -v ON_ERROR_STOP=1 -qc "INSERT INTO schema_migrations(name) VALUES ('$name')"
done
echo "migrations up to date"

#!/bin/sh
# Logical PostgreSQL backup. Run from a host with pg_dump and keep output encrypted off-host.
set -eu
: "${DATABASE_URL:?DATABASE_URL is required}"
out=${1:?usage: backup_postgres.sh /secure/path/verireturn-YYYYMMDD.dump}
pg_dump --format=custom --no-owner --file "$out" "$DATABASE_URL"
printf 'backup=%s\n' "$out"

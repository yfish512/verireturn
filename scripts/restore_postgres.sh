#!/bin/sh
# Deliberately requires an explicit target URL; restore only into a rehearsed empty database.
set -eu
: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is required}"
in=${1:?usage: restore_postgres.sh /secure/path/verireturn.dump}
pg_restore --clean --if-exists --no-owner --dbname "$RESTORE_DATABASE_URL" "$in"
printf 'restored=%s\n' "$in"

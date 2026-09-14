#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
data_dir="$project_dir/.local/postgres"
port="${POSTGRES_PORT:-54329}"
db_name="${POSTGRES_DB:-verireturn}"
db_user="${POSTGRES_USER:-verireturn}"

if ! command -v initdb >/dev/null 2>&1; then
  echo "未找到 initdb。请先用 Miniforge 更新环境：mamba env update -f environment.yml" >&2
  exit 1
fi

case "${1:-start}" in
  start)
    mkdir -p "$data_dir"
    if [ ! -f "$data_dir/PG_VERSION" ]; then
      initdb -D "$data_dir" --username="$db_user" --auth=trust
    fi
    if ! pg_ctl -D "$data_dir" status >/dev/null 2>&1; then
      pg_ctl -D "$data_dir" -o "-p $port" -l "$data_dir/postgres.log" start
    fi
    if ! psql -h 127.0.0.1 -p "$port" -U "$db_user" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$db_name'" | grep -q 1; then
      createdb -h 127.0.0.1 -p "$port" -U "$db_user" "$db_name"
    fi
    echo "PostgreSQL 已启动：postgresql+psycopg://${db_user}@127.0.0.1:${port}/${db_name}"
    ;;
  stop)
    pg_ctl -D "$data_dir" stop
    ;;
  *)
    echo "用法：$0 [start|stop]" >&2
    exit 1
    ;;
esac

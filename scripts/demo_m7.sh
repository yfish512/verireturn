#!/bin/sh
# Convenience entrypoint for the HTTP-only M7 portfolio flow.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
mamba_bin="${MAMBA_BIN:-/data/user004/miniforge3/bin/mamba}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn}"
exec "$mamba_bin" run -n verireturn python -m scripts.demo_m7

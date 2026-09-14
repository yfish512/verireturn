#!/bin/sh
# Convenience entrypoint for the HTTP-only M7 portfolio flow.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
mamba_bin="${MAMBA_BIN:-mamba}"
if ! command -v "$mamba_bin" >/dev/null 2>&1; then
  echo "未找到 mamba。请先将 Miniforge 的 bin 目录加入 PATH，或设置 MAMBA_BIN。" >&2
  exit 1
fi
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn}"
exec "$mamba_bin" run -n verireturn python -m scripts.demo_m7

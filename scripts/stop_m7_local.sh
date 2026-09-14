#!/bin/sh
# Stop only processes whose PID files were created by start_m7_local.sh.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
pid_dir="$project_dir/.local/m7/pids"

if [ ! -d "$pid_dir" ]; then
  echo "No M7 PID directory exists."
  exit 0
fi

for pid_file in "$pid_dir"/*.pid; do
  [ -f "$pid_file" ] || continue
  pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "stopped $(basename "$pid_file" .pid) (pid $pid)"
  fi
done

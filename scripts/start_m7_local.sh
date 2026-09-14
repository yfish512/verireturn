#!/bin/sh
# Start the M7 portfolio demo without Docker.  Only processes started here are
# recorded in .local/m7/pids and may be stopped by stop_m7_local.sh.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
runtime_dir="$project_dir/.local/m7"
pid_dir="$runtime_dir/pids"
log_dir="$runtime_dir/logs"
mkdir -p "$pid_dir" "$log_dir"

mamba_bin="${MAMBA_BIN:-/data/user004/miniforge3/bin/mamba}"
# The API's webhook verifier deliberately reads its secret from the process
# environment rather than a settings object.  Export the local, git-ignored
# development values once for every process started by this script.
if [ -f "$project_dir/.env" ]; then
  set -a
  . "$project_dir/.env"
  set +a
fi
database_url="${DATABASE_URL:-postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn}"
export DATABASE_URL="$database_url"
export AGENT_SERVICE_URL="${AGENT_SERVICE_URL:-http://127.0.0.1:8000}"
export EVAL_BASE_URL="${EVAL_BASE_URL:-http://127.0.0.1:8000}"

if [ "${M7_AGENT_MODE:-live}" = "deterministic" ]; then
  # Empty OS variables take precedence over .env, selecting the existing
  # keyword extractor while retaining the same bounded tool graph.
  export LLM_BASE_URL="" LLM_API_KEY="" LLM_MODEL=""
fi

"$mamba_bin" run -n verireturn sh scripts/local_postgres.sh start
"$mamba_bin" run -n verireturn alembic upgrade head
"$mamba_bin" run -n verireturn python -m scripts.seed_m7_scenario

start_process() {
  name=$1
  shift
  pid_file="$pid_dir/$name.pid"
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "$name already running (pid $(cat "$pid_file"))"
    return
  fi
  nohup "$@" > "$log_dir/$name.log" 2>&1 &
  echo "$!" > "$pid_file"
}

start_process api "$mamba_bin" run -n verireturn uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
start_process fulfillment-worker "$mamba_bin" run -n verireturn python -m scripts.run_worker_daemon fulfillment
start_process knowledge-worker "$mamba_bin" run -n verireturn python -m scripts.run_worker_daemon knowledge
start_process metrics-worker "$mamba_bin" run -n verireturn python -m scripts.run_worker_daemon metrics
start_process evaluation-worker "$mamba_bin" run -n verireturn python -m scripts.run_worker_daemon evaluation

if [ -d frontend/node_modules ]; then
  start_process frontend sh -c 'cd frontend && npm run dev -- --host 127.0.0.1 --port 5173'
else
  echo "frontend/node_modules is missing; run npm ci in frontend before opening the operations console." >&2
fi

attempt=0
until curl -fsS http://127.0.0.1:8000/health >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "API did not become healthy; see $log_dir/api.log" >&2
    exit 1
  fi
  sleep 1
done

echo "M7 local runtime is ready"
echo "  API: http://127.0.0.1:8000/docs"
echo "  Ops console: http://127.0.0.1:5173"
echo "  Demo: DATABASE_URL='$database_url' $mamba_bin run -n verireturn python -m scripts.demo_m7"

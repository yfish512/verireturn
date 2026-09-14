#!/bin/sh
set -eu

base_url="${VERIRETURN_API_URL:-http://127.0.0.1:8000}"
for attempt in $(seq 1 30); do
  if curl -fsS "$base_url/health" >/dev/null; then break; fi
  [ "$attempt" = "30" ] && { echo "API 未就绪：$base_url" >&2; exit 1; }
  sleep 2
done

end=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
start=$(date -u -d '10 minutes ago' +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -v-10M +"%Y-%m-%dT%H:%M:%SZ")
curl -fsS -X POST "$base_url/ops/metrics/jobs" \
  -H 'Content-Type: application/json' -H 'X-Demo-User-Id: OPS_MANAGER' \
  --data "{\"window_start\":\"$start\",\"window_end\":\"$end\"}" >/dev/null
sleep 3
curl -fsS "$base_url/ops/observability/overview" -H 'X-Demo-User-Id: OPS001'
printf '\n运营台：http://127.0.0.1:5173\nPrometheus：http://127.0.0.1:9090\n'

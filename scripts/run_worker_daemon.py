"""Long-running Compose worker wrapper around durable, lease-based job drains."""
from __future__ import annotations

import importlib
import sys
import time


RUNNERS = {
    "metrics": "scripts.run_metrics_worker",
    "fulfillment": "scripts.run_fulfillment_worker",
    "knowledge": "scripts.run_knowledge_worker",
    "evaluation": "scripts.run_evaluation_worker",
    "maintenance": "scripts.run_maintenance_worker",
    "review-sla": "scripts.run_review_sla_worker",
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in RUNNERS:
        raise SystemExit(f"usage: python -m scripts.run_worker_daemon {{{', '.join(RUNNERS)}}}")
    worker_name = sys.argv[1]
    runner = importlib.import_module(RUNNERS[worker_name]).main
    # Individual workers may parse their own CLI flags (for example the
    # knowledge worker supports --once).  Do not leak this daemon's selector
    # into the imported module on every durable drain iteration.
    sys.argv = [sys.argv[0]]
    while True:
        try:
            runner()
        except Exception as exc:  # the job itself persists failure evidence before raising
            print(f"worker={worker_name} error={type(exc).__name__}: {exc}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()

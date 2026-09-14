"""Run M4 PostgreSQL-backed knowledge ingestion jobs.

Use this as a separately supervised process in deployment. The worker claims
jobs with `FOR UPDATE SKIP LOCKED`; several copies may run safely.
"""

from __future__ import annotations

import argparse

from backend.app.database import SessionLocal
from backend.app.domain.knowledge import process_one_ingestion_job


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="处理一条任务后退出")
    args = parser.parse_args()
    completed = 0
    while True:
        with SessionLocal() as db:
            job_id = process_one_ingestion_job(db)
        if job_id is None:
            break
        completed += 1
        print(f"indexed knowledge ingestion job {job_id}")
        if args.once:
            break
    print(f"knowledge worker completed {completed} job(s)")


if __name__ == "__main__":
    main()

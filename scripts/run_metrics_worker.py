"""Drain M6 metric computation jobs; safe to run in multiple processes."""
from backend.app.database import SessionLocal
from backend.app.domain.observability import process_one_metric_job


def main() -> None:
    count = 0
    while True:
        with SessionLocal() as db:
            job_id = process_one_metric_job(db)
        if job_id is None:
            break
        count += 1
    print(f"processed_metric_jobs={count}")


if __name__ == "__main__":
    main()

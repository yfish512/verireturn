"""Execute one queued M6 evaluation run with a checksum-pinned evaluator suite."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
from backend.app.database import SessionLocal
from backend.app.domain.observability import claim_one_evaluation_run, finish_evaluation_run

RUNNERS = {"m2": "evals.run_m2_evals", "m3": "evals.run_m3_evals", "m4": "evals.run_m4_evals", "m5": "evals.run_m5_evals"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with SessionLocal() as db:
        claimed = claim_one_evaluation_run(db)
    if claimed is None:
        print("processed_evaluation_runs=0")
        return
    run, suite = claimed
    report_path = None
    try:
        case_path = (PROJECT_ROOT / suite.case_path).resolve()
        if not case_path.is_relative_to(PROJECT_ROOT) or not case_path.is_file():
            raise RuntimeError("EVALUATION_SUITE_PATH_INVALID")
        if hashlib.sha256(case_path.read_bytes()).hexdigest() != suite.checksum:
            raise RuntimeError("EVALUATION_SUITE_CHECKSUM_MISMATCH")
        with tempfile.NamedTemporaryFile(prefix="verireturn-eval-", suffix=".json", delete=False) as report_file:
            report_path = report_file.name
        env = os.environ.copy()
        env["LLM_MODEL"] = run.model_name
        command = [sys.executable, "-m", RUNNERS[suite.suite_key], "--base-url", env.get("EVAL_BASE_URL", "http://127.0.0.1:8000"), "--database-url", env["DATABASE_URL"], "--cases", str(case_path.relative_to(PROJECT_ROOT)), "--report", report_path]
        result = subprocess.run(command, cwd=PROJECT_ROOT, env=env, text=True, capture_output=True, timeout=900)
        report = json.loads(Path(report_path).read_text(encoding="utf-8")) if Path(report_path).exists() else None
        with SessionLocal() as db:
            finish_evaluation_run(db, run.id, report, None if result.returncode == 0 else (result.stderr or result.stdout)[-2000:])
    except Exception as exc:
        with SessionLocal() as db:
            finish_evaluation_run(db, run.id, None, str(exc))
        raise
    finally:
        if report_path:
            Path(report_path).unlink(missing_ok=True)
    print("processed_evaluation_runs=1")


if __name__ == "__main__":
    main()

"""Run isolated, reproducible Recovery policy comparisons."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from backend.app.recovery.evaluator import evaluate

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="evals/cases/recovery_agent_cases.jsonl")
    parser.add_argument("--report")
    parser.add_argument("--planner", choices=("bounded", "llm"), default="bounded")
    parser.add_argument("--model-name", default=None)
    args = parser.parse_args()
    cases = [json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = evaluate(cases, planner_mode=args.planner, model_name=args.model_name)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["failed"] == 0 else 1
if __name__ == "__main__": raise SystemExit(main())

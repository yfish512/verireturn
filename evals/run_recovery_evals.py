"""Run budgeted, resumable Recovery comparisons, streaming every finished episode."""
from __future__ import annotations
import argparse, json, hashlib, os
from pathlib import Path
from backend.app.recovery.evaluator import evaluate
from backend.app.recovery.evaluate_v2 import evaluate_v2, PROMPT_VERSION, implementation_checksum


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cases',default='evals/cases/recovery_agent_cases.jsonl')
    parser.add_argument('--report',default='/tmp/verireturn-recovery.json')
    parser.add_argument('--planner',choices=('bounded','llm'),default='bounded')
    parser.add_argument('--model-name')
    parser.add_argument('--strategies',nargs='+')
    parser.add_argument('--repetitions',type=int,default=1)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--split',choices=('development','holdout'))
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--work-dir')
    args=parser.parse_args()
    cases=[json.loads(line) for line in Path(args.cases).read_text(encoding='utf-8').splitlines() if line.strip()]
    if args.split:cases=[c for c in cases if c.get('split')==args.split]
    if args.limit:cases=cases[:args.limit]
    output=Path(args.report);output.parent.mkdir(parents=True,exist_ok=True)
    stream=output.with_suffix('.results.jsonl');manifest=output.with_suffix('.manifest.json')
    metadata={'cases':hashlib.sha256(json.dumps(cases,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        'planner':args.planner,'model':args.model_name,'strategies':args.strategies,'repetitions':args.repetitions,'prompt':PROMPT_VERSION,'implementation_checksum':implementation_checksum()}
    existing=[]
    if args.resume and manifest.exists():
        if json.loads(manifest.read_text())!=metadata:raise ValueError('RESUME_MANIFEST_MISMATCH')
        if stream.exists():existing=[json.loads(line) for line in stream.read_text().splitlines() if line]
    elif stream.exists():raise ValueError('Report exists; use --resume or choose a new path')
    manifest.write_text(json.dumps(metadata,indent=2)+'\n')
    def on_result(row):
        with stream.open('a',encoding='utf-8') as file:
            file.write(json.dumps(row,ensure_ascii=False)+'\n');file.flush();os.fsync(file.fileno())
        print(f"{row['id']} {row['status']}",flush=True)
    if cases and cases[0].get('schema_version')==2:
        report=evaluate_v2(cases,planner_mode=args.planner,model_name=args.model_name,strategies=args.strategies,
            repetitions=args.repetitions,existing=existing,on_result=on_result,work_dir=args.work_dir or str(output.with_suffix('.fixtures')))
    else:report=evaluate(cases,planner_mode=args.planner,model_name=args.model_name)
    tmp=output.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');tmp.replace(output)
    print(json.dumps({k:v for k,v in report.items() if k not in {'results'}},ensure_ascii=False,indent=2))
    return int(report['failed']>0 or not report.get('complete',True))

if __name__=='__main__':raise SystemExit(main())

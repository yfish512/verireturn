"""Import already-produced CLI evidence into the development operations display.

This never starts an experiment or makes a model request. The import checksum,
source protocol and actual archived prompt are retained rather than relabelled.
"""
import argparse,gzip,hashlib,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from backend.app.auth import AuthSettings
from backend.app.database import SessionLocal
from backend.app.models import EvaluationRun,EvaluationSuite
from backend.app.domain.observability import finish_evaluation_run


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prefix',default='docs/recovery-evidence/formal-v2');args=parser.parse_args()
    if AuthSettings().app_env.lower()=='production':raise RuntimeError('ARCHIVE_IMPORT_DISABLED')
    prefix=Path(args.prefix);summary=prefix.with_suffix('.summary.json').read_bytes();raw=prefix.with_suffix('.results.jsonl.gz').read_bytes()
    report=json.loads(summary);report['results']=[json.loads(line) for line in gzip.decompress(raw).decode().splitlines()]
    if report['total']!=len(report['results']) or len({r['id'] for r in report['results']})!=report['total']:raise ValueError('ARCHIVE_RESULT_COUNT_INVALID')
    checksum=hashlib.sha256(summary+raw).hexdigest()
    with SessionLocal() as db:
        existing=db.scalar(select(EvaluationRun).where(EvaluationRun.options_json['import_checksum'].as_string()==checksum))
        if existing:
            if existing.status=='failed':finish_evaluation_run(db,existing.id,report)
            print(json.dumps({'id':existing.id,'status':existing.status,'already_imported':True}));return
        suite=None
        for s in db.scalars(select(EvaluationSuite).where(EvaluationSuite.suite_key=='recovery').order_by(EvaluationSuite.version.desc())):
            if s.content_snapshot:
                cases=[json.loads(line) for line in s.content_snapshot.splitlines() if line.strip()]
                if hashlib.sha256(json.dumps(cases,sort_keys=True,ensure_ascii=False).encode()).hexdigest()==report['corpus_checksum']:suite=s;break
        if not suite:raise ValueError('ARCHIVE_SUITE_SNAPSHOT_NOT_FOUND')
        token=str(uuid4());run=EvaluationRun(id=str(uuid4()),suite_id=suite.id,requested_by='OPS_MANAGER',
            model_name=report['model_name'],prompt_version=report['prompt_version'],status='failed',attempts=0,last_error='ARCHIVE_IMPORT_IN_PROGRESS',
            options_json={'planner_mode':report['planner_mode'],'source':'archived_cli_import','import_checksum':checksum,'archive':str(prefix),'protocol':'docs/recovery-evidence/protocol.json'})
        db.add(run);db.commit()
        finish_evaluation_run(db,run.id,report,lease_token=token)
        print(json.dumps({'id':run.id,'status':run.status,'results':report['total'],'source':'archived_cli_import'}))

if __name__=='__main__':main()

"""Checksum-pinned evaluation with heartbeats, fenced result commits and resume."""
from __future__ import annotations
import hashlib,json,os,subprocess,sys,tempfile,threading
from pathlib import Path
from backend.app.database import SessionLocal
from backend.app.domain.observability import (claim_one_evaluation_run,finish_evaluation_run,
    append_evaluation_result,heartbeat_evaluation,list_evaluation_results)
RUNNERS={"m2":"evals.run_m2_evals","m3":"evals.run_m3_evals","m4":"evals.run_m4_evals","m5":"evals.run_m5_evals","recovery":"evals.run_recovery_evals"}
ROOT=Path(__file__).resolve().parents[1]


def main():
    with SessionLocal() as db:claimed=claim_one_evaluation_run(db)
    if not claimed:print('processed_evaluation_runs=0');return
    run,suite=claimed;stop=threading.Event();lost=[]
    def heartbeat():
        while not stop.wait(20):
            try:
                with SessionLocal() as db:heartbeat_evaluation(db,run.id,run.lease_token)
            except Exception as exc:lost.append(exc);return
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    try:
        case_path=(ROOT/suite.case_path).resolve()
        content=suite.content_snapshot.encode() if suite.content_snapshot is not None else case_path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=suite.checksum:raise RuntimeError('EVALUATION_SUITE_CHECKSUM_MISMATCH')
        cases=[json.loads(line) for line in content.decode().splitlines() if line.strip()]
        if suite.suite_key=='recovery' and cases[0].get('schema_version')==2:
            from backend.app.recovery.evaluate_v2 import evaluate_v2, implementation_checksum
            pinned=(run.options_json or {}).get('implementation_checksum')
            if pinned and pinned!=implementation_checksum():raise RuntimeError('EVALUATION_IMPLEMENTATION_CHECKSUM_MISMATCH')
            existing=[]
            with SessionLocal() as db:
                while True:
                    page=list_evaluation_results(db,run.id,offset=len(existing),limit=1000)
                    existing.extend({'id':r.case_id,**r.evidence_json} for r in page)
                    if len(page)<1000:break
            def append(item):
                if lost:raise RuntimeError('EVALUATION_LEASE_LOST')
                with SessionLocal() as db:append_evaluation_result(db,run.id,run.lease_token,item)
            opts=run.options_json or {}
            report=evaluate_v2(cases,planner_mode=opts.get('planner_mode','bounded'),model_name=run.model_name,
                strategies=opts.get('strategies'),repetitions=opts.get('repetitions',1),max_models=opts.get('max_model_calls',4),
                max_tools=opts.get('max_tool_calls',8),existing=existing,on_result=append,
                work_dir=ROOT/'.runtime'/'recovery'/run.id)
        else:
            with tempfile.TemporaryDirectory(prefix='verireturn-eval-') as temp:
                cases_file=Path(temp)/'cases.jsonl';cases_file.write_bytes(content);report_path=Path(temp)/'report.json'
                command=[sys.executable,'-m',RUNNERS[suite.suite_key],'--cases',str(cases_file),'--report',str(report_path)]
                if suite.suite_key!='recovery':command += ['--base-url',os.getenv('EVAL_BASE_URL','http://127.0.0.1:8000'),'--database-url',os.environ['DATABASE_URL']]
                env=os.environ.copy();env['LLM_MODEL']=run.model_name
                result=subprocess.run(command,cwd=ROOT,env=env,text=True,capture_output=True,timeout=900)
                if result.returncode:raise RuntimeError((result.stderr or result.stdout)[-1000:])
                report=json.loads(report_path.read_text())
        with SessionLocal() as db:finish_evaluation_run(db,run.id,report,lease_token=run.lease_token)
    except Exception as exc:
        with SessionLocal() as db:
            try:finish_evaluation_run(db,run.id,None,type(exc).__name__+':'+str(exc)[:500],lease_token=run.lease_token)
            except Exception:pass  # A stale worker must never overwrite the new owner.
        raise
    finally:stop.set();thread.join(timeout=1)
    print('processed_evaluation_runs=1')

if __name__=='__main__':main()

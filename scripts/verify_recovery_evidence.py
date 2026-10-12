"""Offline integrity and final-fact verification; makes no model/API requests."""
import gzip,hashlib,json
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1];path=root/'docs/recovery-evidence'
    manifest=json.loads((path/'SHA256.json').read_text())
    for name,digest in manifest.items():
        assert hashlib.sha256((path/name).read_bytes()).hexdigest()==digest,name
    rows=[json.loads(line) for line in gzip.decompress((path/'formal-v2.results.jsonl.gz').read_bytes()).decode().splitlines()]
    assert len(rows)==600 and len({r['id'] for r in rows})==600
    counts={}
    for strategy in ('react_baseline','recovery_agent'):
        group=[r for r in rows if r['strategy']==strategy]
        assert len(group)==300
        for r in group:
            f=r['final_state'];assert not r['business_violations']
            assert f['refunded_quantity']<=f['quantity']
            assert all(s['available']>=s['reserved']>=0 for s in f['stocks'])
            if r['business_completed']:
                assert f['case_status']=='completed'
                if f['request_type']=='refund':
                    assert f['refund_status']==f['channel']['status']=='succeeded'
                    assert f['refunded_quantity']==f['quantity'] and f['refund_amount']==f['channel']['amount']
                else:assert f['exchange_status']=='delivered'
        counts[strategy]={'completed':sum(r['business_completed'] for r in group),
            'false_completion_claims':sum(r.get('false_completion_claims',0) for r in group),
            'blocked_actions':sum(r.get('blocked_actions',0) for r in group),
            'model_calls':sum(r['model_calls'] for r in group)}
    summary=json.loads((path/'formal-v2.summary.json').read_text())
    for s,c in counts.items():assert c['completed']==summary['metrics_by_strategy'][s]['completed_tasks']
    assert counts['react_baseline']['completed']==200 and counts['recovery_agent']['completed']==249
    budget=json.loads((path/'budget.json').read_text());assert budget['used_calls']<=budget['max_calls']==3000
    print(json.dumps({'hashes_verified':len(manifest),'formal_results':len(rows),'counts':counts,'budget':budget},ensure_ascii=False,indent=2))

if __name__=='__main__':main()

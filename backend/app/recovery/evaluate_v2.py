"""Paired evaluation with incremental results, independent verdicts and honest model errors."""
import hashlib, json, math, random, time
from collections import defaultdict
from pathlib import Path
from .world import World
from .engine import Engine
from .model import RulePlanner, LivePlanner
from .budget import ModelBudget

OFFLINE_STRATEGIES=("baseline","fixed_retry","recovery_agent")
LIVE_STRATEGIES=("react_baseline","recovery_agent")
PROMPT_VERSION="recovery-prompt-v3"


def implementation_checksum():
    root=Path(__file__).resolve().parents[3]
    files=sorted((root/"backend/app/recovery").glob("*.py"))
    files += [root/"backend/app"/name for name in ("models.py","security.py","domain/service.py","domain/resolutions.py","domain/payments.py","domain/exchange.py","domain/fulfillment.py")]
    digest=hashlib.sha256()
    for file in files:
        digest.update(str(file.relative_to(root)).encode());digest.update(file.read_bytes())
    return digest.hexdigest()


def wilson(successes,n):
    if not n: return [0,0]
    z=1.96;p=successes/n;d=1+z*z/n
    middle=(p+z*z/(2*n))/d;radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [round(max(0,middle-radius),4),round(min(1,middle+radius),4)]


def aggregate(rows):
    n=len(rows)
    if not n:return {"tasks":0}
    metric={"tasks":n,"completed_tasks":sum(r["business_completed"] for r in rows)}
    for name,outcomes in {"autonomous_success_rate":{"autonomous_success"},"assisted_success_rate":{"assisted_success"},
            "safe_handoff_rate":{"safe_handoff"},"unsafe_failure_rate":{"unsafe_failure"},
            "awaiting_reconfirmation_rate":{"awaiting_reconfirmation"},"waiting_stock_rate":{"waiting_stock"},
            "model_error_rate":{"model_error"},"budget_exhausted_rate":{"budget_exhausted"}}.items():
        metric[name]=round(sum(r["outcome"] in outcomes for r in rows)/n,4)
    metric["system_escalation_rate"]=round(sum(r.get("system_escalation",False) for r in rows)/n,4)
    metric["business_completion_rate"]=round(metric["completed_tasks"]/n,4)
    metric["completion_95ci"]=wilson(metric["completed_tasks"],n)
    metric["expected_terminal_rate"]=round(sum(r["matches_expected"] for r in rows)/n,4)
    metric["fault_recovery_rate"]=round(sum(r["matches_expected"] and not r["business_violations"] for r in rows)/n,4)
    for field in ("tool_calls","extra_tool_calls","model_calls","input_tokens","output_tokens","duration_ms","blocked_actions","incorrect_actions","false_completion_claims"):
        metric["total_"+field]=sum(r.get(field,0) for r in rows)
        metric["average_"+field]=round(metric["total_"+field]/n,2)
    metric["business_violations"]=sum(len(r["business_violations"]) for r in rows)
    return metric


def summarize(cases,rows,mode,model_name,budget=None):
    groups=defaultdict(list);family=defaultdict(list);split=defaultdict(list)
    for r in rows:
        groups[r["strategy"]].append(r);family[(r["strategy"],r["scenario_id"])].append(r);split[(r["strategy"],r["split"])].append(r)
    report={"schema_version":2,"suite":"recovery","planner_mode":mode,"model_name":model_name,
        "prompt_version":PROMPT_VERSION,"implementation_checksum":implementation_checksum(),"episodes":len(cases),"total":len(rows),"failed":sum(r["status"]=="harness_error" for r in rows),
        "completed":len(rows),"complete":True,"scenario_families":sorted({c["scenario_id"] for c in cases}),
        "corpus_checksum":hashlib.sha256(json.dumps(cases,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        "results":rows,"metrics_by_strategy":{k:aggregate(v) for k,v in groups.items()},
        "metrics_by_family":{f"{k[0]}:{k[1]}":aggregate(v) for k,v in family.items()},
        "metrics_by_split":{f"{k[0]}:{k[1]}":aggregate(v) for k,v in split.items()},"budget":budget}
    paired={r["episode_id"]:{ } for r in rows}
    for r in rows:paired[r["episode_id"]][r["strategy"]]=r
    comparator="react_baseline" if mode=="llm" else "fixed_retry"
    diffs=[int(v["recovery_agent"]["business_completed"])-int(v[comparator]["business_completed"]) for v in paired.values() if "recovery_agent" in v and comparator in v]
    if diffs:
        rng=random.Random(20261012);samples=sorted(sum(rng.choices(diffs,k=len(diffs)))/len(diffs) for _ in range(2000))
        report["paired_completion_difference"]={"comparator":comparator,"pairs":len(diffs),"difference":round(sum(diffs)/len(diffs),4),"bootstrap_95ci":[round(samples[50],4),round(samples[1949],4)]}
    return report


def evaluate_v2(cases,*,planner_mode="bounded",model_name=None,strategies=None,repetitions=1,
                on_result=None,existing=None,work_dir=None,budget=None,max_models=4,max_tools=8):
    cases=list(cases);rows=list(existing or [])
    strategies=tuple(strategies or (LIVE_STRATEGIES if planner_mode=="llm" else OFFLINE_STRATEGIES))
    if not set(strategies)<=set((*OFFLINE_STRATEGIES,"react_baseline")):raise ValueError("unknown strategy")
    if planner_mode!="llm" and "react_baseline" in strategies:raise ValueError("react_baseline requires a live model")
    planner=RulePlanner()
    if planner_mode=="llm":
        from ..agent.runtime import AgentSettings
        from ..database import SessionLocal
        settings=AgentSettings();model_name=model_name or settings.llm_model
        if not all((settings.llm_base_url,settings.llm_api_key,model_name)):raise ValueError("RECOVERY_LLM_CONFIGURATION_REQUIRED")
        budget=budget or ModelBudget(SessionLocal)
        planner=LivePlanner(settings.llm_base_url,settings.llm_api_key,model_name,budget)
    seen={r["id"] for r in rows}
    for case in cases:
        # Cost reference uses the same fixture without faults; it is not a model baseline.
        clean={**case,"no_fault":True}
        world=World(clean)
        try:cost=Engine(world,"recovery_agent",RulePlanner()).run()["tool_calls"]
        finally:world.close()
        for repeat in range(repetitions):
            for strategy in strategies:
                result_id=f"{case['id']}:{strategy}:r{repeat}"
                if result_id in seen:continue
                start=time.perf_counter();usage_start=len(planner.usage)
                directory=Path(work_dir)/hashlib.sha256(result_id.encode()).hexdigest() if work_dir else None
                world=None
                try:
                    world=World(case,directory)
                    selected=planner if strategy in LIVE_STRATEGIES else RulePlanner()
                    result=Engine(world,strategy,selected,max_models=max_models,max_tools=max_tools).run()
                except Exception as error:
                    result={"status":"harness_error","outcome":"harness_error","business_completed":False,"matches_expected":False,
                        "business_violations":[],"error_type":type(error).__name__,"error":str(error)[:300]}
                finally:
                    if world:world.close()
                usage=planner.usage[usage_start:]
                row={"id":result_id,"episode_id":f"{case['id']}:r{repeat}","scenario_id":case["scenario_id"],"variant":case["variant"],
                    "seed":case["seed"],"split":case["split"],"strategy":strategy,"repeat":repeat,
                    "duration_ms":int((time.perf_counter()-start)*1000),"usage":usage,**result,
                    "planning_steps":result.get("model_calls",0),"model_calls":len(usage),
                    "input_tokens":sum(u["input_tokens"] for u in usage),"output_tokens":sum(u["output_tokens"] for u in usage),
                    "extra_tool_calls":result.get("tool_calls",0)-cost,"no_fault_reference_tool_calls":cost}
                rows.append(row);seen.add(result_id)
                if on_result:on_result(row)
                if budget and budget.snapshot()["used_calls"]>=budget.snapshot()["max_calls"]:
                    report=summarize(cases,rows,planner_mode,model_name,budget.snapshot())
                    report.update(expected_results=len(cases)*len(strategies)*repetitions,complete=len(rows)==len(cases)*len(strategies)*repetitions)
                    return report
    report=summarize(cases,rows,planner_mode,model_name,budget.snapshot() if budget else None)
    report.update(expected_results=len(cases)*len(strategies)*repetitions,complete=len(rows)==len(cases)*len(strategies)*repetitions)
    return report

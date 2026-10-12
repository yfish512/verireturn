"""Small synchronous comparisons; full suites run through the evaluation worker."""
import random
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..auth import ActorContext, AuthSettings, require_operator
from ..database import get_db
from ..recovery.corpus import generate_cases, VARIANTS
from ..recovery.evaluate_v2 import evaluate_v2
from ..recovery.scenarios import SCENARIOS
from ..schemas import RecoveryCompareRequest

recovery_router = APIRouter(prefix="/ops/recovery", tags=["recovery"])

@recovery_router.get("/scenarios")
def list_recovery_scenarios(_: ActorContext = Depends(require_operator)):
    return [{"id": scenario_id, **detail, "variants": [v for v,f in VARIANTS.items() if f == scenario_id]}
            for scenario_id, detail in SCENARIOS.items()]

@recovery_router.post("/compare")
def compare_recovery_policies(body: RecoveryCompareRequest, _: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    del db  # All business fixtures and channel records are isolated from customer data.
    if AuthSettings().app_env.lower() == "production":
        raise HTTPException(403, detail={"code": "FAULT_DEMO_DISABLED", "message": "生产环境禁用故障演示。"})
    scenarios = body.scenario_ids or list(SCENARIOS)
    unknown = set(scenarios) - set(SCENARIOS)
    if unknown or len(set(scenarios)) != len(scenarios):
        raise HTTPException(422, detail={"code": "RECOVERY_SCENARIO_UNKNOWN", "message": "场景必须有效且不能重复。"})
    corpus = generate_cases()
    rng = random.Random(body.seed)
    cases = [rng.choice([c for c in corpus if c["scenario_id"] == scenario]) for scenario in scenarios]
    try:
        return evaluate_v2(cases, planner_mode=body.planner_mode, model_name=body.model_name)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "RECOVERY_CONFIGURATION_INVALID", "message": str(exc)}) from exc

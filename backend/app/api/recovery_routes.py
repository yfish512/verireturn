from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..auth import ActorContext, require_operator
from ..database import get_db
from ..recovery.evaluator import default_cases, evaluate
from ..recovery.scenarios import SCENARIOS
from ..schemas import RecoveryCompareRequest

recovery_router = APIRouter(prefix="/ops/recovery", tags=["recovery"])

@recovery_router.get("/scenarios")
def list_recovery_scenarios(_: ActorContext = Depends(require_operator)):
    return [{"id": scenario_id, **detail} for scenario_id, detail in SCENARIOS.items()]

@recovery_router.post("/compare")
def compare_recovery_policies(body: RecoveryCompareRequest, _: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    del db  # Experiments use isolated fixtures; production records are never mutated.
    scenarios = body.scenario_ids or list(SCENARIOS)
    unknown = set(scenarios) - set(SCENARIOS)
    if unknown:
        raise HTTPException(422, detail={"code": "RECOVERY_SCENARIO_UNKNOWN", "message": f"未知恢复场景：{', '.join(sorted(unknown))}。"})
    cases = [{"id": f"api-{scenario}-{body.seed}", "scenario_id": scenario, "seed": body.seed} for scenario in scenarios]
    return evaluate(cases, planner_mode=body.planner_mode, model_name=body.model_name)

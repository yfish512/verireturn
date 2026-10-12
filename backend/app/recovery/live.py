"""Production recovery adapter. Never injects faults or fabricates channel progress."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select
from ..models import RecoveryExecution, RecoveryStep, IdempotencyRecord, AfterSalesCase, AgentThread, AgentTask
from ..domain.service import DomainError
from ..domain.resolutions import resolution_options
from ..domain.fulfillment import _create_incident
from ..security import redact_text
from .engine import Engine
from .model import RulePlanner, LivePlanner
from .budget import ModelBudget

OPERATIONS={"get_order","get_order_items","get_logistics","get_fulfillment_status","check_after_sales_eligibility",
    "create_after_sales_case","confirm_after_sales_case","cancel_after_sales_case","schedule_pickup"}
WRITES={"create_after_sales_case","confirm_after_sales_case","cancel_after_sales_case","schedule_pickup"}
RETRYABLE={"M1_NETWORK_ERROR","M1_HTTP_ERROR","TOOL_TEMPORARILY_UNAVAILABLE"}

class LiveWorld:
    is_live=True
    intent_id=None
    def __init__(self,factory,execution_id,tools):
        self.factory,self.execution_id,self.tools=factory,execution_id,tools
        with factory() as db:
            row=db.get(RecoveryExecution,execution_id);meta=row.state_json["live"]
            self.actor=row.actor_id;self.case_id=row.case_id;self.meta=meta
            self.case={"user_goal":meta["goal"],"initial":{"quantity":meta.get("quantity",1),"sku":"客户当前商品"}}
            self.task_context={"operation":meta["operation"],"arguments":meta["arguments"]}

    def primary(self,occurrence):
        from ..agent.tools import ToolError
        m=self.meta;args=m["arguments"];actor=self.actor;rid=f"recovery-{self.execution_id}:{occurrence}";key=m["idempotency_key"]
        dispatch={
            "get_order":lambda:self.tools.get_order(actor,args["order_id"],rid),
            "get_order_items":lambda:self.tools.get_order_items(actor,args["order_id"],rid),
            "get_logistics":lambda:self.tools.get_logistics(actor,args["order_id"],rid),
            "get_fulfillment_status":lambda:self.tools.get_fulfillment(actor,args["case_id"],rid),
            "check_after_sales_eligibility":lambda:self.tools.check_eligibility(actor,args["order_id"],args["request_type"],args.get("reason","售后查询"),rid),
            "create_after_sales_case":lambda:self.tools.create_case(actor,args["order_id"],args["request_type"],args["reason"],args.get("items"),rid,key),
            "confirm_after_sales_case":lambda:self.tools.confirm_case(actor,args["case_id"],rid,key),
            "cancel_after_sales_case":lambda:self.tools.cancel_case(actor,args["case_id"],rid,key),
            "schedule_pickup":lambda:self.tools.schedule_pickup(actor,args["case_id"],args["time_slot"],rid,key),
        }
        try:result=dispatch[m["operation"]]()
        except ToolError as error:
            return {"error":"result_unknown" if m["operation"] in WRITES else "temporarily_unavailable","message":error.message}
        if isinstance(result,dict) and result.get("status")=="fulfillment_exception":
            return {"error":"stock_changed","message":"当前库存不足，请重新选择方案。"}
        return {"terminal":"tool_recovered","result":result}

    def verify(self,occurrence):
        if self.meta["operation"] not in WRITES:return self.primary(occurrence)
        with self.factory() as db:
            operation=self.meta["operation"]
            record=db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.actor_id==self.actor,
                IdempotencyRecord.operation==operation,IdempotencyRecord.idempotency_key==self.meta["idempotency_key"]))
            if not record:
                return {"error":"result_unknown","message":"尚未发现原命令执行凭据，可能仍在执行，不会重复提交。"}
            case=db.get(AfterSalesCase,int(record.resource_id))
            if not case or case.user_id!=self.actor:raise DomainError("CASE_NOT_FOUND","售后单不存在。",404)
            result={"id":case.id,"status":case.status,"eligible_amount":str(case.eligible_amount),"request_type":case.request_type}
            if case.status=="fulfillment_exception":return {"error":"stock_changed","message":"原命令已执行，库存不足，需重新选择方案。"}
            return {"terminal":"tool_recovered","result":result,"status":"accepted"}

    def refresh(self,occurrence):
        with self.factory() as db:return resolution_options(db,self.actor,self.case_id)
    def offer(self):return {"terminal":"awaiting_customer","message":"请选择退款、可用换货方案、等待补货或人工处理，并再次确认。"}
    def poll(self):return self.verify(1)
    def handoff(self):
        if self.case_id:
            with self.factory() as db:
                _create_incident(db,self.case_id,"RECOVERY_HUMAN_REQUIRED","客户售后恢复需要人工处理。",f"live-recovery:{self.execution_id}");db.commit()
            return {"terminal":"human","message":"已创建人工处理工单。"}
        return {"terminal":"stopped","message":"业务系统暂不可用，请稍后重试。"}
    def facts(self):
        with self.factory() as db:
            case=db.get(AfterSalesCase,self.case_id) if self.case_id else None
            return {"case_id":self.case_id,"case_status":case.status if case else None}
    def verdict(self,state):
        status=state.get("terminal","stopped")
        return {"outcome":status,"business_completed":False,"matches_expected":False,"business_violations":[],
            "blocked_actions":state.get("blocked_actions",0),"incorrect_actions":state.get("incorrect_actions",0)}


def create_live_recovery(factory,state,operation,args,*,observation=None,command_key=None):
    if operation not in OPERATIONS:raise DomainError("RECOVERY_TOOL_UNSUPPORTED","此工具尚不支持自动恢复。",422)
    key=command_key or f"{state['run_id']}:{operation}"
    with factory() as db:
        # Existing owner lock serializes creation of the same command across workers.
        db.scalar(select(AgentThread).where(AgentThread.id==state["thread_id"],AgentThread.actor_id==state["actor_id"]).with_for_update())
        row=db.scalar(select(RecoveryExecution).where(RecoveryExecution.actor_id==state["actor_id"],RecoveryExecution.command_key==key))
        if row:return row.id
        initial=observation or {"error":"temporarily_unavailable","message":"原工具调用未完成。"}
        engine={"observation":initial,"trace":[{"sequence_no":1,"kind":"observation","detail":{"action":"primary","result":initial}}],
            "tool_calls":1,"primary_calls":1,"pending_write":operation in WRITES and initial.get("error")=="result_unknown"}
        case_id=args.get("case_id") or state.get("case_id")
        if case_id:
            case=db.get(AfterSalesCase,case_id)
            if not case or case.user_id!=state["actor_id"]:raise DomainError("CASE_NOT_FOUND","售后单不存在。",404)
        # Original domain idempotency key is reconstructed by the existing helper.
        from ..agent.graph import _stable_key
        suffix={"create_after_sales_case":"create","confirm_after_sales_case":"confirm","cancel_after_sales_case":"cancel","schedule_pickup":"schedule_pickup"}.get(operation,operation)
        args = {k: redact_text(v) if k == "reason" and isinstance(v,str) else v for k,v in args.items()}
        meta={"operation":operation,"arguments":args,"idempotency_key":_stable_key(state,suffix),"goal":redact_text(state["message"])}
        row=RecoveryExecution(id=str(uuid4()),actor_id=state["actor_id"],thread_id=state["thread_id"],case_id=case_id,command_key=key,
            state_json={"live":meta,"engine":engine});db.add(row);db.flush()
        db.add(RecoveryStep(id=str(uuid4()),execution_id=row.id,sequence_no=1,kind="observation",detail_json=engine["trace"][0]["detail"]));db.commit()
        return row.id


def owned_execution(db,actor,execution_id):
    row=db.get(RecoveryExecution,execution_id)
    if not row or row.actor_id!=actor:raise DomainError("RECOVERY_NOT_FOUND","恢复任务不存在。",404)
    return row


def run_live(factory,actor,execution_id,tools,planner=None,*,expected_version=None):
    now=datetime.now(timezone.utc);token=str(uuid4())
    with factory() as db:
        row=db.scalar(select(RecoveryExecution).where(RecoveryExecution.id==execution_id,RecoveryExecution.actor_id==actor).with_for_update())
        if not row:raise DomainError("RECOVERY_NOT_FOUND","恢复任务不存在。",404)
        if expected_version is not None and row.version != expected_version:
            raise DomainError("RECOVERY_CONFLICT", "任务版本已变化。", 409)
        if row.status in {"applied","waiting_stock","human","restock_available"}:
            return {"terminal":row.status,"response":"请查看最新售后状态；新方案仍需客户确认。","tool_result":None}
        if row.status=="model_error":
            state=dict(row.state_json);engine=dict(state.get("engine",{}));engine.pop("terminal",None);state["engine"]=engine;row.state_json=state
        if row.status=="cancelled":raise DomainError("RECOVERY_CANCELLED","任务已取消。",409)
        expiry=row.lease_until.replace(tzinfo=timezone.utc) if row.lease_until and row.lease_until.tzinfo is None else row.lease_until
        if expiry and expiry>now:raise DomainError("RECOVERY_BUSY","恢复任务正在执行。",409)
        row.lease_token,row.lease_until=token,now+timedelta(seconds=180);db.commit()
    try:
        if planner is None:
            from ..agent.runtime import AgentSettings
            settings=AgentSettings()
            planner=LivePlanner(settings.llm_base_url,settings.llm_api_key,settings.llm_model,ModelBudget(factory)) if settings.llm_api_key else RulePlanner()
        world=LiveWorld(factory,execution_id,tools);world.lease_token=token
        return Engine(world,"recovery_agent",planner).run()
    finally:
        with factory() as db:
            row=db.scalar(select(RecoveryExecution).where(RecoveryExecution.id==execution_id).with_for_update())
            if row.lease_token==token:row.lease_until=None;row.lease_token=None;db.commit()

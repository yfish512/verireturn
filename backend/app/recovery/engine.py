"""Multi-step LangGraph execution; durable observations precede model decisions."""
import time
from uuid import uuid4
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from sqlalchemy import select
from ..models import RecoveryExecution, RecoveryStep
from ..security import redact_text
from .model import ACTIONS, ModelFailure
from .budget import BudgetExhausted
from .judge import judge

class State(TypedDict,total=False):
    observation: dict
    trace: list
    tool_calls: int
    model_calls: int
    primary_calls: int
    verify_calls: int
    refresh_calls: int
    retries: int
    decision: dict
    last_action: str
    pending_write: bool
    not_executed: bool
    blocked_actions: int
    incorrect_actions: int
    terminal: str
    dispatching: bool
    claimed_complete: bool
    elapsed: float
    system_escalation: bool
    system_actions: int

class Engine:
    def __init__(self,world,strategy,planner,*,max_tools=8,max_models=4,max_seconds=120):
        self.world,self.strategy,self.planner=world,strategy,planner
        self.max_tools,self.max_models,self.max_seconds=max_tools,max_models,max_seconds
        self.started=time.monotonic();self.prior_elapsed=0

    def save(self,state,kind,detail):
        trace=[*state.get("trace",[]),{"sequence_no":len(state.get("trace",[]))+1,"kind":kind,"detail":detail}]
        state={**state,"trace":trace,"elapsed":self.prior_elapsed+time.monotonic()-self.started}
        with self.world.factory() as db:
            row=db.get(RecoveryExecution,self.world.execution_id)
            if getattr(self.world,"is_live",False):
                expiry = row.lease_until
                from datetime import datetime, timezone
                if expiry and expiry.tzinfo is None: expiry=expiry.replace(tzinfo=timezone.utc)
                if row.lease_token != self.world.lease_token or not expiry or expiry <= datetime.now(timezone.utc):
                    raise RuntimeError("RECOVERY_LEASE_LOST")
            live_meta = row.state_json.get("live")
            row.state_json={"world":{"case_id":self.world.case_id,"intent_id":self.world.intent_id},"engine":state, **({"live":live_meta} if live_meta else {})}
            row.version+=1;row.status=state.get("terminal","running")
            db.add(RecoveryStep(id=str(uuid4()),execution_id=row.id,sequence_no=len(trace),kind=kind,detail_json=detail));db.commit()
        return state

    def execute(self,state):
        if state.get("terminal"): return state
        if state.get("trace"):
            # A process may have died after dispatch but before saving its result.
            if state.get("dispatching"):
                return self.save({**state,"observation":{"error":"result_unknown","message":"进程中断，先核验原操作。"},"pending_write":True,"dispatching":False},"restart",{})
            return state
        state=self.save({**state,"dispatching":True,"primary_calls":1,"tool_calls":1},"dispatch",{"action":"primary"})
        observation=self.world.primary(1)
        return self.observe(state,observation,"primary")

    def observe(self,state,observation,action):
        state={**state,"dispatching":False,"observation":observation,"last_action":action}
        if observation.get("error")=="result_unknown": state["pending_write"]=True
        if observation.get("status")=="not_found" and observation.get("authoritative"):
            state["not_executed"]=True;state["pending_write"]=False
        if observation.get("status") in {"submitted","accepted","completed"}: state["pending_write"]=False
        if observation.get("status")=="completed": state["terminal"]="completed"
        if observation.get("terminal"): state["terminal"]=observation["terminal"]
        return self.save(state,"observation",{"action":action,"result":observation})

    def allowed(self,state):
        # Same business/tool availability for all strategies. No oracle label is used.
        actions=["verify_execution_status","handoff_to_human","stop_safely","finish"]
        if state.get("retries",0)<2: actions.append("retry_tool")
        if self.world.intent_id: actions.append("wait_for_result")
        if state.get("observation",{}).get("error")=="stock_changed" or "options" in state.get("observation",{}):
            actions += ["refresh_business_state","offer_alternative"]
        if state.get("last_action")=="refresh_business_state": actions += ["refresh_business_state","offer_alternative"]
        return sorted(set(actions))

    def stop_for_budget(self,state,scope):
        state=self.save({**state,"terminal":"budget_exhausted"},"budget",{"scope":scope})
        # Budget escalation is an explicit system command, common to all strategies;
        # it neither resets budgets nor silently replaces the model with a rule planner.
        result=self.world.handoff()
        return self.save({**state,"system_escalation":result.get("terminal")=="human",
            "system_actions":state.get("system_actions",0)+1,
            "observation":{"message":result.get("message") or ("恢复预算已用尽，已创建人工处理工单。" if result.get("terminal")=="human" else "恢复预算已用尽，任务已安全停止。")}},
            "system_escalation",{"reason":"budget_exhausted","result":result})

    def plan(self,state):
        if (state.get("tool_calls",0)>=self.max_tools or state.get("model_calls",0)>=self.max_models
                or self.prior_elapsed+time.monotonic()-self.started>=self.max_seconds):
            return self.stop_for_budget(state,"episode")
        context={"user_goal":self.world.case["user_goal"],
            "task": getattr(self.world,"task_context", {"operation": "refund" if self.world.intent_id else "exchange", "quantity": self.world.case["initial"]["quantity"], "sku": self.world.case["initial"]["sku"]}),"observation":state["observation"],
            "last_action":state.get("last_action"),"allowed_actions":self.allowed(state),
            "history":[event["detail"] for event in state.get("trace",[]) if event["kind"] in {"decision","observation","blocked"}][-8:],
            "remaining":{"tools":self.max_tools-state.get("tool_calls",0),"plans":self.max_models-state.get("model_calls",0)}}
        if self.strategy=="recovery_agent":
            context["execution_evidence"]={"pending_write":state.get("pending_write",False),"verified_not_executed":state.get("not_executed",False),"retries":state.get("retries",0),"verification_calls":state.get("verify_calls",0)}
        # Reserve local call count before dispatch, so crash cannot restore budget.
        state=self.save({**state,"model_calls":state.get("model_calls",0)+1},"planning",{})
        try: decision=self.planner.choose(self.strategy,context)
        except BudgetExhausted:
            return self.stop_for_budget(state,"global")
        except ModelFailure as error:
            return self.save({**state,"terminal":"model_error","incorrect_actions":state.get("incorrect_actions",0)+int(error.invalid_action)},
                "model_error",{"error":str(error),"usage":self.planner.usage[-1] if self.planner.usage else None})
        if decision.get("action") not in self.allowed(state):
            return self.save({**state,"terminal":"model_error","incorrect_actions":state.get("incorrect_actions",0)+1},"invalid_action",decision)
        return self.save({**state,"decision":decision},"decision",{**decision, "usage": self.planner.usage[-1] if self.planner.usage else None})

    def act(self,state):
        if state.get("terminal"): return state
        action=state["decision"]["action"]
        if action=="stop_safely": return self.save({**state,"terminal":"stopped"},"stop",{})
        if action=="finish": return self.save({**state,"terminal":"stopped","claimed_complete":True},"completion_claim",{})
        if action=="retry_tool" and (state.get("pending_write") or self.world.intent_id and not state.get("not_executed") and state.get("primary_calls",0)>0):
            return self.observe({**state,"blocked_actions":state.get("blocked_actions",0)+1,"incorrect_actions":state.get("incorrect_actions",0)+1},
                {"error":"result_unknown","message":"系统拦截重试：尚未获得可信未执行证据，先核验原命令。"},action)
        state={**state,"tool_calls":state.get("tool_calls",0)+1}
        if action=="retry_tool": state.update(retries=state.get("retries",0)+1,primary_calls=state.get("primary_calls",0)+1)
        if action=="verify_execution_status": state["verify_calls"]=state.get("verify_calls",0)+1
        if action=="refresh_business_state": state["refresh_calls"]=state.get("refresh_calls",0)+1
        state=self.save({**state,"dispatching":True},"dispatch",{"action":action})
        try:
            result={"retry_tool":lambda:self.world.primary(state["primary_calls"]),
                "verify_execution_status":lambda:self.world.verify(state["verify_calls"]),
                "refresh_business_state":lambda:self.world.refresh(state["refresh_calls"]),
                "offer_alternative":self.world.offer,"wait_for_result":self.world.poll,"handoff_to_human":self.world.handoff}[action]()
        except Exception as error:
            from ..domain.service import DomainError
            if not isinstance(error,DomainError): raise
            result={"error":"business_rejected","code":error.code,"message":error.message}
            state["incorrect_actions"]=state.get("incorrect_actions",0)+1
        return self.observe(state,result,action)

    def run(self):
        with self.world.factory() as db:
            state=db.get(RecoveryExecution,self.world.execution_id).state_json.get("engine",{})
        self.prior_elapsed=state.get("elapsed",0)
        if state.get("terminal")=="budget_exhausted" and not state.get("system_actions"):
            state=self.stop_for_budget(state,"restart")
        graph=StateGraph(State);graph.add_node("execute",self.execute);graph.add_node("plan",self.plan);graph.add_node("act",self.act)
        graph.add_edge(START,"execute")
        graph.add_conditional_edges("execute",lambda s: END if s.get("terminal") else "plan")
        graph.add_conditional_edges("plan",lambda s: END if s.get("terminal") else "act")
        graph.add_conditional_edges("act",lambda s: END if s.get("terminal") else "plan")
        final=graph.compile().invoke(state,{"recursion_limit":40})
        facts=self.world.facts();verdict=self.world.verdict(final) if getattr(self.world,"is_live",False) else judge(self.world.case,facts,final)
        return {**verdict,"status":verdict["outcome"],"trace":final.get("trace",[]),"final_state":facts,
            "tool_calls":final.get("tool_calls",0),"model_calls":final.get("model_calls",0),
            "retries":final.get("retries",0),"verification_calls":final.get("verify_calls",0),
            "system_actions":final.get("system_actions",0),"system_escalation":final.get("system_escalation",False),
            "terminal":final.get("terminal"),"tool_result":final.get("observation",{}).get("result"),"response":final.get("observation",{}).get("message","请查看可信业务状态。")}

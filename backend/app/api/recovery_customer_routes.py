"""Owned recovery queries and explicitly confirmed after-sales alternatives."""
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from ..auth import current_demo_user
from ..database import SessionLocal, get_db
from ..models import RecoveryExecution, RecoveryStep, AgentTask, AgentThread, AfterSalesResolution, AfterSalesCase
from ..domain.service import DomainError
from ..domain.resolutions import resolution_options, propose_resolution, confirm_resolution
from ..agent.runtime import get_agent_runtime
from ..agent.tools import M1ToolClient
from ..recovery.live import owned_execution, run_live

customer_recovery_router=APIRouter(tags=['customer-recovery'])
class Version(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1)
class ResolutionProposal(BaseModel):
    model_config=ConfigDict(extra='forbid')
    option:str=Field(pattern='^(refund|exchange|wait|human)$')
    replacement_sku:str|None=Field(default=None,max_length=64)
class ResolutionConfirmation(Version):
    approved:bool

def error(exc):return HTTPException(exc.status_code,detail={'code':exc.code,'message':exc.message})
def resolution_dict(row):
    return {'id':row.id,'case_id':row.case_id,'option':row.option,'replacement_sku':row.replacement_sku,
        'status':row.status,'version':row.version,'expires_at':row.expires_at,'linked_case_id':row.linked_case_id}

@customer_recovery_router.get('/agent/recoveries/{execution_id}')
def read_recovery(execution_id:str,actor:str=Depends(current_demo_user),db=Depends(get_db),limit:int=Query(30,ge=1,le=100),before:int|None=Query(None,ge=1)):
    try:row=owned_execution(db,actor,execution_id)
    except DomainError as exc:raise error(exc)
    query=select(RecoveryStep).where(RecoveryStep.execution_id==execution_id)
    if before:query=query.where(RecoveryStep.sequence_no<before)
    steps=list(db.scalars(query.order_by(RecoveryStep.sequence_no.desc()).limit(limit+1)))
    more=len(steps)>limit;steps=steps[:limit];steps.reverse()
    options=None
    if row.case_id:
        case=db.get(AfterSalesCase,row.case_id)
        if case and case.status=='fulfillment_exception':options=resolution_options(db,actor,row.case_id)['options']
    return {'id':row.id,'status':row.status,'version':row.version,'case_id':row.case_id,'options':options,
        'steps':[{'sequence_no':s.sequence_no,'kind':s.kind,'detail':s.detail_json} for s in steps],
        'next_before':steps[0].sequence_no if more and steps else None}

@customer_recovery_router.post('/agent/recoveries/{execution_id}/resume')
def resume_recovery(execution_id:str,body:Version,actor:str=Depends(current_demo_user),db=Depends(get_db)):
    try:
        row=owned_execution(db,actor,execution_id)
        if row.version!=body.expected_version:raise DomainError('RECOVERY_CONFLICT','任务版本已变化。',409)
        runtime=get_agent_runtime()
        with runtime.memory.execution_lock(row.thread_id):
            result=run_live(SessionLocal,actor,execution_id,M1ToolClient('http://127.0.0.1:8000'),expected_version=body.expected_version)
            runtime.memory.append_control_reply(row.thread_id,{'response':result['response'],'recovery_id':execution_id,'recovery_status':result['terminal']})
        return result
    except DomainError as exc:raise error(exc)

@customer_recovery_router.post('/agent/recoveries/{execution_id}/cancel')
def cancel_recovery(execution_id:str,body:Version,actor:str=Depends(current_demo_user),db=Depends(get_db)):
    try:
        row=db.scalar(select(RecoveryExecution).where(RecoveryExecution.id==execution_id,RecoveryExecution.actor_id==actor).with_for_update())
        if not row:raise DomainError('RECOVERY_NOT_FOUND','恢复任务不存在。',404)
        if row.version!=body.expected_version:raise DomainError('RECOVERY_CONFLICT','任务版本已变化。',409)
        from datetime import datetime,timezone
        expiry=row.lease_until
        if expiry and expiry.tzinfo is None:expiry=expiry.replace(tzinfo=timezone.utc)
        if expiry and expiry>datetime.now(timezone.utc):raise DomainError('RECOVERY_BUSY','任务正在执行。',409)
        state=dict(row.state_json);state['engine']={**state.get('engine',{}),'terminal':'cancelled'}
        row.state_json,row.status,row.version=state,'cancelled',row.version+1;db.commit()
        if row.thread_id:get_agent_runtime().memory.append_control_reply(row.thread_id,{'response':'已停止自动恢复。','recovery_id':row.id,'recovery_status':'cancelled'})
        return {'id':row.id,'status':row.status,'version':row.version}
    except DomainError as exc:raise error(exc)

@customer_recovery_router.get('/tools/after-sales/cases/{case_id}/resolution-options')
def read_options(case_id:int,actor:str=Depends(current_demo_user),db=Depends(get_db)):
    try:return resolution_options(db,actor,case_id)
    except DomainError as exc:raise error(exc)

@customer_recovery_router.post('/tools/after-sales/cases/{case_id}/resolutions')
def propose(case_id:int,body:ResolutionProposal,actor:str=Depends(current_demo_user),db=Depends(get_db),key:str=Header(alias='Idempotency-Key',min_length=8,max_length=128)):
    try:return resolution_dict(propose_resolution(db,actor,case_id,body.option,key,body.replacement_sku))
    except DomainError as exc:raise error(exc)

@customer_recovery_router.post('/tools/after-sales/resolutions/{resolution_id}/confirm')
def confirm(resolution_id:str,body:ResolutionConfirmation,actor:str=Depends(current_demo_user),db=Depends(get_db)):
    try:
        row=confirm_resolution(db,actor,resolution_id,body.expected_version,body.approved)
        result=resolution_dict(row)
        # Project the committed business fact to the matching task, never the unrelated focus.
        for execution in db.scalars(select(RecoveryExecution).where(RecoveryExecution.actor_id==actor,RecoveryExecution.case_id==row.case_id)):
            if execution.thread_id:
                tasks=list(db.scalars(select(AgentTask).where(AgentTask.thread_id==execution.thread_id,AgentTask.active_case_id==row.case_id)))
                for task in tasks:
                    if row.status=='waiting_stock':
                        task.phase='waiting_stock';task.missing_slots=[];task.version+=1
                    if row.status=='applied':
                        target=row.linked_case_id or row.case_id
                        task.active_case_id=target;task.intent='schedule_pickup';task.phase='awaiting_pickup_slot';task.missing_slots=['time_slot']
                        task.slots_json={**task.slots_json,'case_id':target,'request_type':db.get(AfterSalesCase,target).request_type,'time_slot':None};task.version+=1
                execution.status=row.status;execution.version+=1
        db.commit()
        return result
    except DomainError as exc:db.rollback();raise error(exc)

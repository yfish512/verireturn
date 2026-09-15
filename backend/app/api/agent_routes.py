from fastapi import APIRouter, Depends, HTTPException, Query

from ..agent.runtime import AgentRuntime, AgentRuntimeError, get_agent_runtime
from ..agent.schemas import AgentMessageRequest, AgentMessageResponse, AgentTaskCancelResponse, AgentThreadCreateResponse, AgentThreadSnapshotResponse, AgentToolCallResponse, ConfirmationRequest, AgentTaskFocusRequest, AgentTaskResponse
from ..auth import current_demo_user


router = APIRouter(prefix="/agent", tags=["agent-runtime"])


def runtime_error(error: AgentRuntimeError) -> HTTPException:
    status = 404 if error.code in {"CONFIRMATION_NOT_FOUND", "THREAD_NOT_FOUND"} else 409 if error.code in {"CONFIRMATION_EXPIRED", "CONFIRMATION_ALREADY_RESOLVED", "TASK_NOT_ACTIVE"} else 500
    return HTTPException(status_code=status, detail={"code": error.code, "message": error.message})


@router.post("/threads", response_model=AgentThreadCreateResponse, status_code=201)
def create_agent_thread(
    actor_id: str = Depends(current_demo_user),
    runtime: AgentRuntime = Depends(get_agent_runtime),
):
    return runtime.create_thread(actor_id)


@router.get("/threads/{thread_id}", response_model=AgentThreadSnapshotResponse)
def read_agent_thread(
    thread_id: str,
    actor_id: str = Depends(current_demo_user),
    runtime: AgentRuntime = Depends(get_agent_runtime),
    before_sequence: int | None = Query(default=None, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
):
    try:
        return runtime.thread_snapshot(thread_id, actor_id, before_sequence=before_sequence, limit=limit)
    except AgentRuntimeError as error:
        raise runtime_error(error) from error


@router.post("/threads/{thread_id}/messages", response_model=AgentMessageResponse)
def send_agent_message(
    thread_id: str,
    request: AgentMessageRequest,
    actor_id: str = Depends(current_demo_user),
    runtime: AgentRuntime = Depends(get_agent_runtime),
):
    try:
        return runtime.handle_message(thread_id, actor_id, request.message, request.message_id)
    except AgentRuntimeError as error:
        raise runtime_error(error) from error


@router.get("/threads/{thread_id}/tasks", response_model=list[dict])
def list_agent_tasks(thread_id: str, actor_id: str = Depends(current_demo_user), runtime: AgentRuntime = Depends(get_agent_runtime)):
    try:
        return runtime.list_tasks(thread_id, actor_id)
    except AgentRuntimeError as error:
        raise runtime_error(error) from error


@router.post("/threads/{thread_id}/tasks/{task_id}/focus", response_model=AgentTaskResponse)
def focus_agent_task(thread_id: str, task_id: str, request: AgentTaskFocusRequest, actor_id: str = Depends(current_demo_user), runtime: AgentRuntime = Depends(get_agent_runtime)):
    try:
        return {"task": runtime.focus_task(thread_id, actor_id, task_id, restore=request.restore)}
    except AgentRuntimeError as error:
        raise runtime_error(error) from error


@router.post("/threads/{thread_id}/tasks/{task_id}/archive", response_model=AgentTaskResponse)
def archive_agent_task(thread_id: str, task_id: str, actor_id: str = Depends(current_demo_user), runtime: AgentRuntime = Depends(get_agent_runtime)):
    try:
        return {"task": runtime.archive_task(thread_id, actor_id, task_id)}
    except AgentRuntimeError as error:
        raise runtime_error(error) from error


@router.post("/threads/{thread_id}/task/cancel", response_model=AgentTaskCancelResponse)
def cancel_agent_task(
    thread_id: str,
    actor_id: str = Depends(current_demo_user),
    runtime: AgentRuntime = Depends(get_agent_runtime),
):
    try:
        return runtime.cancel_task(thread_id, actor_id)
    except AgentRuntimeError as error:
        raise runtime_error(error) from error


@router.post("/confirmations/{confirmation_id}", response_model=AgentMessageResponse)
def resolve_agent_confirmation(
    confirmation_id: str,
    request: ConfirmationRequest,
    actor_id: str = Depends(current_demo_user),
    runtime: AgentRuntime = Depends(get_agent_runtime),
):
    try:
        return runtime.resolve_confirmation(confirmation_id, actor_id, request.approved)
    except AgentRuntimeError as error:
        raise runtime_error(error) from error


@router.get("/runs/{run_id}/tool-calls", response_model=list[AgentToolCallResponse])
def read_agent_tool_calls(
    run_id: str,
    actor_id: str = Depends(current_demo_user),
    runtime: AgentRuntime = Depends(get_agent_runtime),
):
    return runtime.traces.list_tool_calls(run_id, actor_id)

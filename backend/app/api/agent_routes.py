from fastapi import APIRouter, Depends, HTTPException

from ..agent.runtime import AgentRuntime, AgentRuntimeError, get_agent_runtime
from ..agent.schemas import AgentMessageRequest, AgentMessageResponse, AgentToolCallResponse, ConfirmationRequest
from ..auth import current_demo_user


router = APIRouter(prefix="/agent", tags=["agent-runtime"])


def runtime_error(error: AgentRuntimeError) -> HTTPException:
    status = 404 if error.code == "CONFIRMATION_NOT_FOUND" else 409 if error.code in {"CONFIRMATION_EXPIRED", "CONFIRMATION_ALREADY_RESOLVED"} else 500
    return HTTPException(status_code=status, detail={"code": error.code, "message": error.message})


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

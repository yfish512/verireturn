from __future__ import annotations

import os
from contextlib import ExitStack
from functools import lru_cache
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..database import DATABASE_URL, SessionLocal
from .graph import AgentGraph
from .intent import KeywordIntentExtractor, OpenAIIntentExtractor
from .knowledge import AgentKnowledgeService, ExtractiveAnswerGenerator, OpenAIGroundedAnswerGenerator
from .tools import M1ToolClient
from .trace import TraceStore


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    agent_service_url: str = "http://127.0.0.1:8000"
    agent_confirmation_ttl_seconds: int = 900
    agent_tool_timeout_seconds: float = 8.0
    knowledge_top_k: int = 4


class AgentRuntimeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class AgentRuntime:
    def __init__(self, graph, traces: TraceStore, extractor, resource_stack: ExitStack | None = None):
        self.graph = graph
        self.traces = traces
        self.extractor = extractor
        self.resource_stack = resource_stack

    def close(self) -> None:
        if self.resource_stack is not None:
            self.resource_stack.close()

    def handle_message(self, thread_id: str, actor_id: str, message: str, message_id: str | None = None) -> dict:
        pending = self.traces.get_pending_confirmation(thread_id, actor_id)
        if pending is not None:
            return {
                "thread_id": thread_id,
                "run_id": pending.run_id,
                "status": "awaiting_confirmation",
                "response": f"售后单 #{pending.case_id} 正等待您的确认，请先确认或取消当前申请。",
                "confirmation_id": pending.id,
                "case_id": pending.case_id,
                "ticket_id": None,
                "retrieval_id": None,
                "citations": [],
            }
        pending_review = self.traces.get_pending_review_confirmation(thread_id, actor_id)
        if pending_review is not None:
            return {
                "thread_id": thread_id,
                "run_id": pending_review.run_id,
                "status": "awaiting_confirmation",
                "response": "人工审核申请正等待您的确认，请先确认或取消当前申请。",
                "confirmation_id": pending_review.id,
                "case_id": None,
                "ticket_id": None,
                "retrieval_id": None,
                "citations": [],
            }
        run_id = self.traces.start_run(thread_id, actor_id, self.extractor.model_name, message)
        message_id = message_id or str(uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        try:
            result = self.graph.invoke(
                {"thread_id": thread_id, "run_id": run_id, "actor_id": actor_id, "message": message, "message_id": message_id, "tool_sequence": 0},
                config,
            )
        except Exception as error:
            self.traces.finish_run(run_id, "failed", "Agent 执行失败，请稍后重试。", "AGENT_RUNTIME_ERROR")
            raise AgentRuntimeError("AGENT_RUNTIME_ERROR", str(error)) from error
        if result.get("__interrupt__"):
            return {
                "thread_id": thread_id,
                "run_id": run_id,
                "status": "awaiting_confirmation",
                "response": result["final_response"],
                "confirmation_id": result.get("confirmation_id"),
                "case_id": result.get("case_id"),
                "ticket_id": result.get("review_ticket_id"),
                "retrieval_id": result.get("retrieval_id"),
                "citations": result.get("citations", []),
            }
        return {
            "thread_id": thread_id,
            "run_id": run_id,
            "status": "completed",
            "response": result.get("final_response", "处理完成。"),
            "confirmation_id": None,
            "case_id": result.get("case_id"),
            "ticket_id": result.get("review_ticket_id"),
            "retrieval_id": result.get("retrieval_id"),
            "citations": result.get("citations", []),
        }

    def resolve_confirmation(self, confirmation_id: str, actor_id: str, approved: bool) -> dict:
        confirmation = self.traces.get_confirmation(confirmation_id, actor_id)
        review_confirmation = None if confirmation is not None else self.traces.get_review_confirmation(confirmation_id, actor_id)
        if confirmation is None and review_confirmation is None:
            raise AgentRuntimeError("CONFIRMATION_NOT_FOUND", "确认请求不存在或不属于当前用户。")
        active_confirmation = confirmation or review_confirmation
        if active_confirmation.status == "expired":
            raise AgentRuntimeError("CONFIRMATION_EXPIRED", "确认请求已过期，请重新发起售后。")
        if active_confirmation.status != "pending":
            raise AgentRuntimeError("CONFIRMATION_ALREADY_RESOLVED", "确认请求已经处理。")
        config = {"configurable": {"thread_id": active_confirmation.thread_id}}
        try:
            result = self.graph.invoke(Command(resume={"approved": approved}), config)
        except Exception as error:
            self.traces.finish_run(active_confirmation.run_id, "failed", "确认处理失败，请稍后重试。", "CONFIRMATION_RUNTIME_ERROR")
            raise AgentRuntimeError("CONFIRMATION_RUNTIME_ERROR", str(error)) from error
        return {
            "thread_id": active_confirmation.thread_id,
            "run_id": active_confirmation.run_id,
            "status": "completed",
            "response": result.get("final_response", "确认处理完成。"),
            "confirmation_id": confirmation_id,
            "case_id": confirmation.case_id if confirmation is not None else None,
            "ticket_id": result.get("review_ticket_id"),
            "retrieval_id": result.get("retrieval_id"),
            "citations": result.get("citations", []),
        }


def _checkpoint_database_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.removeprefix("postgresql+psycopg://")
    return url


def build_agent_runtime(
    *, settings: AgentSettings | None = None, session_factory=SessionLocal, tools=None, extractor=None, checkpointer=None,
    knowledge_service=None,
) -> AgentRuntime:
    settings = settings or AgentSettings()
    if extractor is None:
        extractor = (
            OpenAIIntentExtractor(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
            if settings.llm_base_url and settings.llm_api_key and settings.llm_model
            else KeywordIntentExtractor()
        )
    tools = tools or M1ToolClient(settings.agent_service_url, settings.agent_tool_timeout_seconds)
    traces = TraceStore(session_factory, settings.agent_confirmation_ttl_seconds)
    if knowledge_service is None:
        answer_generator = (
            OpenAIGroundedAnswerGenerator(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
            if settings.llm_base_url and settings.llm_api_key and settings.llm_model
            else ExtractiveAnswerGenerator()
        )
        knowledge_service = AgentKnowledgeService(session_factory, answer_generator, top_k=settings.knowledge_top_k)
    resource_stack = None
    if checkpointer is None:
        if DATABASE_URL.startswith("postgresql"):
            resource_stack = ExitStack()
            checkpointer = resource_stack.enter_context(PostgresSaver.from_conn_string(_checkpoint_database_url(DATABASE_URL)))
            checkpointer.setup()
        else:
            checkpointer = MemorySaver()
    graph = AgentGraph(extractor, tools, traces, knowledge_service).build(checkpointer)
    return AgentRuntime(graph, traces, extractor, resource_stack)


@lru_cache(maxsize=1)
def get_agent_runtime() -> AgentRuntime:
    return build_agent_runtime()


def close_agent_runtime() -> None:
    runtime = get_agent_runtime.cache_info()
    if runtime.currsize:
        cached = get_agent_runtime()
        cached.close()
        get_agent_runtime.cache_clear()

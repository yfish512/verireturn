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
from .memory import TaskMemory, ThreadAccessError
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
    def __init__(self, graph, traces: TraceStore, extractor, memory: TaskMemory, resource_stack: ExitStack | None = None):
        self.graph = graph
        self.traces = traces
        self.extractor = extractor
        self.memory = memory
        self.resource_stack = resource_stack

    def close(self) -> None:
        if self.resource_stack is not None:
            self.resource_stack.close()

    def create_thread(self, actor_id: str) -> dict:
        thread_id = str(uuid4())
        self.memory.ensure_thread(thread_id, actor_id)
        return {"thread_id": thread_id}

    def thread_snapshot(self, thread_id: str, actor_id: str, *, before_sequence: int | None = None, limit: int = 30) -> dict:
        try:
            return self.memory.snapshot(thread_id, actor_id, before_sequence=before_sequence, limit=limit)
        except ThreadAccessError as error:
            raise AgentRuntimeError("THREAD_NOT_FOUND", str(error)) from error

    def reconcile(self) -> int:
        return self.memory.reconcile_confirmed_after_sales()

    def list_tasks(self, thread_id: str, actor_id: str) -> list[dict]:
        try: return self.memory.list_tasks(thread_id, actor_id)
        except ThreadAccessError as error: raise AgentRuntimeError("THREAD_NOT_FOUND", str(error)) from error

    def focus_task(self, thread_id: str, actor_id: str, task_id: str, *, restore: bool = False) -> dict:
        try: return self.memory.focus_task(thread_id, actor_id, task_id, restore=restore)
        except ThreadAccessError as error: raise AgentRuntimeError("THREAD_NOT_FOUND", str(error)) from error

    def archive_task(self, thread_id: str, actor_id: str, task_id: str) -> dict:
        try: return self.memory.archive_task(thread_id, actor_id, task_id)
        except ThreadAccessError as error: raise AgentRuntimeError("THREAD_NOT_FOUND", str(error)) from error

    def cancel_task(self, thread_id: str, actor_id: str) -> dict:
        pending = self.traces.get_pending_confirmation(thread_id, actor_id)
        if pending is not None:
            resolved = self.resolve_confirmation(pending.id, actor_id, False)
            result = {"thread_id": thread_id, "response": "已取消本次申请。", "task": resolved.get("memory")}
            self.memory.append_control_reply(thread_id, {**resolved, **result})
            return result
        pending_review = self.traces.get_pending_review_confirmation(thread_id, actor_id)
        if pending_review is not None:
            resolved = self.resolve_confirmation(pending_review.id, actor_id, False)
            return {"thread_id": thread_id, "response": "已取消当前人工审核申请。", "task": resolved.get("memory")}
        try:
            task = self.memory.cancel_task(thread_id, actor_id)
        except ThreadAccessError as error:
            raise AgentRuntimeError("THREAD_NOT_FOUND", str(error)) from error
        if task is None:
            raise AgentRuntimeError("TASK_NOT_ACTIVE", "当前没有可取消的任务。")
        result = {"thread_id": thread_id, "response": "已放弃当前任务。", "task": task}
        self.memory.append_control_reply(thread_id, {"response": result["response"], "memory": task})
        return result

    @staticmethod
    def _response(thread_id: str, run_id: str, status: str, response: str, *, confirmation_id=None, case_id=None, ticket_id=None, retrieval_id=None, citations=None, memory=None, last_error_code=None) -> dict:
        result = {
            "thread_id": thread_id, "run_id": run_id, "status": status, "response": response,
            "confirmation_id": confirmation_id, "case_id": case_id, "ticket_id": ticket_id,
            "retrieval_id": retrieval_id, "citations": citations or [], "memory": memory,
        }
        if last_error_code:
            result["last_error_code"] = last_error_code
        return result

    def handle_message(self, thread_id: str, actor_id: str, message: str, message_id: str | None = None) -> dict:
        with self.memory.execution_lock(thread_id):
            return self._handle_message(thread_id, actor_id, message, message_id)

    def _handle_message(self, thread_id: str, actor_id: str, message: str, message_id: str | None = None) -> dict:
        message_id = message_id or str(uuid4())
        try:
            record_id, replay = self.memory.start_message(thread_id, actor_id, message, message_id)
        except ThreadAccessError as error:
            raise AgentRuntimeError("THREAD_NOT_FOUND", str(error)) from error
        if replay is not None:
            return replay

        pending = self.traces.get_pending_confirmation(thread_id, actor_id)
        if pending is not None:
            result = self._response(thread_id, pending.run_id, "awaiting_confirmation", f"售后单 #{pending.case_id} 正等待您的确认，请先确认或取消当前申请。", confirmation_id=pending.id, case_id=pending.case_id, memory=self.memory.snapshot(thread_id, actor_id)["task"])
            self.memory.append_reply(thread_id, record_id, result)
            return result
        pending_review = self.traces.get_pending_review_confirmation(thread_id, actor_id)
        if pending_review is not None:
            result = self._response(thread_id, pending_review.run_id, "awaiting_confirmation", "人工审核申请正等待您的确认，请先确认或取消当前申请。", confirmation_id=pending_review.id, memory=self.memory.snapshot(thread_id, actor_id)["task"])
            self.memory.append_reply(thread_id, record_id, result)
            return result

        try:
            context = self.memory.intent_context(thread_id, actor_id, exclude_message_id=record_id)
            decision = self.extractor.extract(message, context=context)
        except Exception:
            decision = None
        if decision is None:
            run_id = self.traces.start_run(thread_id, actor_id, self.extractor.model_name, message)
            result = self._response(thread_id, run_id, "completed", "暂时无法理解这条售后请求，请提供订单号和具体诉求。")
            self.traces.finish_run(run_id, "completed", result["response"])
            self.memory.append_reply(thread_id, record_id, result)
            return result
        try:
            resolution = self.memory.resolve(thread_id, actor_id, record_id, message, decision)
        except ThreadAccessError as error:
            raise AgentRuntimeError("THREAD_NOT_FOUND", str(error)) from error
        if "reply" in resolution:
            run_id = self.traces.start_run(thread_id, actor_id, self.extractor.model_name, message)
            result = self._response(thread_id, run_id, "completed", resolution["reply"], memory=resolution.get("task"))
            self.traces.finish_run(run_id, "completed", result["response"])
            self.memory.append_reply(thread_id, record_id, result)
            return result

        run_id = self.traces.start_run(thread_id, actor_id, self.extractor.model_name, message)
        graph_input = resolution["graph_input"]
        # Every normal turn gets a fresh graph checkpoint namespace.  A
        # confirmation resume uses its run_id below, preventing stale slots.
        config = {"configurable": {"thread_id": run_id}}
        try:
            graph_result = self.graph.invoke({
                "thread_id": thread_id, "run_id": run_id, "actor_id": actor_id, "message": message,
                "message_id": message_id, "tool_sequence": 0, "intent_resolved": True, **graph_input,
            }, config)
        except Exception as error:
            self.traces.finish_run(run_id, "failed", "Agent 执行失败，请稍后重试。", "AGENT_RUNTIME_ERROR")
            raise AgentRuntimeError("AGENT_RUNTIME_ERROR", str(error)) from error
        if graph_result.get("__interrupt__"):
            result = self._response(thread_id, run_id, "awaiting_confirmation", graph_result["final_response"], confirmation_id=graph_result.get("confirmation_id"), case_id=graph_result.get("case_id"), ticket_id=graph_result.get("review_ticket_id"), retrieval_id=graph_result.get("retrieval_id"), citations=graph_result.get("citations", []))
        else:
            result = self._response(thread_id, run_id, "completed", graph_result.get("final_response", "处理完成。"), case_id=graph_result.get("case_id"), ticket_id=graph_result.get("review_ticket_id"), retrieval_id=graph_result.get("retrieval_id"), citations=graph_result.get("citations", []), last_error_code=graph_result.get("last_error_code"))
        result, memory = self.memory.finish_execution(thread_id, record_id, run_id, result)
        result["memory"] = memory
        result.pop("last_error_code", None)
        self.traces.finish_run(run_id, "awaiting_confirmation" if result["status"] == "awaiting_confirmation" else "completed", result["response"])
        self.memory.append_reply(thread_id, record_id, result)
        return result

    def resolve_confirmation(self, confirmation_id: str, actor_id: str, approved: bool) -> dict:
        confirmation = self.traces.get_confirmation(confirmation_id, actor_id)
        review_confirmation = None if confirmation is not None else self.traces.get_review_confirmation(confirmation_id, actor_id)
        active_confirmation = confirmation or review_confirmation
        if active_confirmation is None:
            raise AgentRuntimeError("CONFIRMATION_NOT_FOUND", "确认请求不存在或不属于当前用户。")
        with self.memory.execution_lock(active_confirmation.thread_id):
            return self._resolve_confirmation(confirmation_id, actor_id, approved)

    def _resolve_confirmation(self, confirmation_id: str, actor_id: str, approved: bool) -> dict:
        confirmation = self.traces.get_confirmation(confirmation_id, actor_id)
        review_confirmation = None if confirmation is not None else self.traces.get_review_confirmation(confirmation_id, actor_id)
        if confirmation is None and review_confirmation is None:
            raise AgentRuntimeError("CONFIRMATION_NOT_FOUND", "确认请求不存在或不属于当前用户。")
        active_confirmation = confirmation or review_confirmation
        if active_confirmation.status == "expired":
            raise AgentRuntimeError("CONFIRMATION_EXPIRED", "确认请求已过期，请重新发起售后。")
        if active_confirmation.status != "pending":
            raise AgentRuntimeError("CONFIRMATION_ALREADY_RESOLVED", "确认请求已经处理。")
        config = {"configurable": {"thread_id": active_confirmation.run_id}}
        try:
            graph_result = self.graph.invoke(Command(resume={"approved": approved}), config)
        except Exception as error:
            self.traces.finish_run(active_confirmation.run_id, "failed", "确认处理失败，请稍后重试。", "CONFIRMATION_RUNTIME_ERROR")
            raise AgentRuntimeError("CONFIRMATION_RUNTIME_ERROR", str(error)) from error
        case_id = confirmation.case_id if confirmation is not None else None
        memory = self.memory.finish_confirmation(active_confirmation.thread_id, actor_id, approved, case_id)
        result = self._response(active_confirmation.thread_id, active_confirmation.run_id, "completed", graph_result.get("final_response", "确认处理完成。"), confirmation_id=confirmation_id, case_id=case_id, ticket_id=graph_result.get("review_ticket_id"), retrieval_id=graph_result.get("retrieval_id"), citations=graph_result.get("citations", []), memory=memory)
        self.memory.append_control_reply(active_confirmation.thread_id, result)
        return result


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
    runtime = AgentRuntime(graph, traces, extractor, TaskMemory(session_factory), resource_stack)
    runtime.reconcile()
    return runtime


@lru_cache(maxsize=1)
def get_agent_runtime() -> AgentRuntime:
    return build_agent_runtime()


def close_agent_runtime() -> None:
    runtime = get_agent_runtime.cache_info()
    if runtime.currsize:
        cached = get_agent_runtime()
        cached.close()
        get_agent_runtime.cache_clear()

"""Durable, explicit task memory for customer conversations.

LangGraph checkpoints resume an interrupted graph.  This module owns the
customer-facing task: its slots, lifecycle and append-only history.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AgentMessageRecord, AgentTask, AgentTaskEvent, AgentThread
from .schemas import IntentDecision

_WRITE_INTENTS = {"create_after_sales", "request_manual_review"}
_SLOT_LABELS = {"order_id": "订单号", "request_type": "售后类型", "case_id": "售后单编号", "time_slot": "取件时段"}


class ThreadAccessError(Exception):
    pass


class TaskMemory:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    @staticmethod
    def _next_sequence(db: Session, model, column, filter_column, value: str) -> int:
        return int(db.scalar(select(func.max(column)).where(filter_column == value)) or 0) + 1

    def ensure_thread(self, thread_id: str, actor_id: str) -> None:
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None:
                db.add(AgentThread(id=thread_id, actor_id=actor_id))
                db.commit()
                return
            if thread.actor_id != actor_id:
                raise ThreadAccessError("会话不存在或不属于当前客户。")
            thread.last_active_at = datetime.now(timezone.utc)
            db.commit()

    def start_message(self, thread_id: str, actor_id: str, content: str, client_message_id: str) -> tuple[str, dict | None]:
        """Persist a user turn before execution; return an existing reply on retry."""
        self.ensure_thread(thread_id, actor_id)
        with self.session_factory() as db:
            existing = db.scalar(select(AgentMessageRecord).where(
                AgentMessageRecord.thread_id == thread_id,
                AgentMessageRecord.client_message_id == client_message_id,
                AgentMessageRecord.role == "customer",
            ))
            if existing is not None:
                reply = db.scalar(select(AgentMessageRecord).where(AgentMessageRecord.reply_to_message_id == existing.id))
                return existing.id, reply.payload_json if reply is not None else None
            sequence = self._next_sequence(db, AgentMessageRecord, AgentMessageRecord.sequence_no, AgentMessageRecord.thread_id, thread_id)
            record = AgentMessageRecord(
                id=str(uuid4()), thread_id=thread_id, sequence_no=sequence, role="customer", content=content,
                client_message_id=client_message_id,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                replay = db.scalar(select(AgentMessageRecord).where(
                    AgentMessageRecord.thread_id == thread_id, AgentMessageRecord.client_message_id == client_message_id,
                    AgentMessageRecord.role == "customer",
                ))
                reply = db.scalar(select(AgentMessageRecord).where(AgentMessageRecord.reply_to_message_id == replay.id)) if replay else None
                return replay.id if replay else record.id, reply.payload_json if reply else None
            return record.id, None

    def append_reply(self, thread_id: str, reply_to_message_id: str, response: dict) -> None:
        with self.session_factory() as db:
            existing = db.scalar(select(AgentMessageRecord).where(AgentMessageRecord.reply_to_message_id == reply_to_message_id))
            if existing is not None:
                return
            sequence = self._next_sequence(db, AgentMessageRecord, AgentMessageRecord.sequence_no, AgentMessageRecord.thread_id, thread_id)
            db.add(AgentMessageRecord(
                id=str(uuid4()), thread_id=thread_id, sequence_no=sequence, role="agent", content=response["response"],
                reply_to_message_id=reply_to_message_id, run_id=response.get("run_id"), payload_json=response,
            ))
            thread = db.get(AgentThread, thread_id)
            if thread is not None:
                thread.last_active_at = datetime.now(timezone.utc)
                thread.memory_version += 1
            db.commit()

    @staticmethod
    def _extract_order(message: str) -> str | None:
        found = re.search(r"\b(O\d+)\b", message, flags=re.IGNORECASE)
        return found.group(1).upper() if found else None

    @staticmethod
    def _required(intent: str) -> list[str]:
        if intent in _WRITE_INTENTS:
            return ["order_id", "request_type"]
        if intent == "schedule_pickup":
            return ["case_id", "time_slot"]
        return []

    @staticmethod
    def _projection(task: AgentTask | None) -> dict | None:
        if task is None:
            return None
        return {
            "task_id": task.id, "intent": task.intent, "phase": task.phase, "slots": task.slots_json,
            "missing_slots": task.missing_slots, "case_id": task.active_case_id, "version": task.version,
        }

    def _event(self, db: Session, task: AgentTask, event_type: str, payload: dict, message_id: str | None) -> None:
        persisted = self._next_sequence(db, AgentTaskEvent, AgentTaskEvent.sequence_no, AgentTaskEvent.task_id, task.id)
        pending = [item.sequence_no for item in db.new if isinstance(item, AgentTaskEvent) and item.task_id == task.id]
        sequence = max([persisted - 1, *pending]) + 1
        db.add(AgentTaskEvent(id=str(uuid4()), task_id=task.id, sequence_no=sequence, event_type=event_type, payload_json=payload, message_id=message_id))

    @staticmethod
    def _clarification(task: AgentTask) -> str:
        missing = task.missing_slots[0]
        if task.intent == "create_after_sales" and missing == "order_id":
            return "可以，请提供订单号，例如 O1001。"
        if task.intent == "request_manual_review" and missing == "order_id":
            return "我可以为您准备人工审核申请。请提供订单号，例如 O1003。"
        if task.intent == "schedule_pickup" and missing == "time_slot":
            return "请提供取件时段，例如“明天上午”。"
        return f"请补充{_SLOT_LABELS.get(missing, missing)}。"

    def resolve(self, thread_id: str, actor_id: str, message_id: str, message: str, decision: IntentDecision) -> dict[str, Any]:
        """Merge a turn into the active task and return either a reply or clean graph input."""
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id:
                raise ThreadAccessError("会话不存在或不属于当前客户。")
            task = db.scalar(select(AgentTask).where(AgentTask.thread_id == thread_id).with_for_update())
            raw_order = self._extract_order(message)
            is_slot_reply = bool(task and (
                (task.phase == "awaiting_pickup_slot" and decision.intent not in _WRITE_INTENTS) or
                (task.phase == "collecting_slots" and (
                    raw_order is not None or decision.time_slot is not None or (decision.request_type is not None and decision.intent not in _WRITE_INTENTS)
                ))
            ))
            starts_new_task = decision.intent in _WRITE_INTENTS or decision.intent == "schedule_pickup"
            if starts_new_task and not is_slot_reply:
                if task is not None and task.phase == "awaiting_customer_confirmation":
                    return {"reply": "当前售后申请正等待您的确认。请先确认或取消后，再发起新的请求。", "task": self._projection(task)}
                slots = {
                    "order_id": decision.order_id, "request_type": decision.request_type,
                    "reason": decision.reason or message if decision.intent in _WRITE_INTENTS else None,
                    "case_id": decision.case_id, "time_slot": decision.time_slot,
                }
                if task is None:
                    task = AgentTask(id=str(uuid4()), thread_id=thread_id, intent=decision.intent, phase="collecting_slots", slots_json=slots, missing_slots=[], version=1)
                    db.add(task)
                    self._event(db, task, "task_created", {"intent": decision.intent, "slots": slots}, message_id)
                else:
                    task.intent, task.phase, task.slots_json, task.active_case_id = decision.intent, "collecting_slots", slots, decision.case_id
                    task.version += 1
                    self._event(db, task, "task_replaced", {"intent": decision.intent, "slots": slots}, message_id)
            elif task is not None and task.phase in {"collecting_slots", "awaiting_pickup_slot"}:
                slots = dict(task.slots_json)
                changed: dict[str, Any] = {}
                if raw_order:
                    slots["order_id"] = raw_order
                    changed["order_id"] = raw_order
                if decision.request_type and not slots.get("request_type"):
                    slots["request_type"] = decision.request_type
                    changed["request_type"] = decision.request_type
                if decision.time_slot:
                    slots["time_slot"] = decision.time_slot
                    changed["time_slot"] = decision.time_slot
                if task.active_case_id and not slots.get("case_id"):
                    slots["case_id"] = task.active_case_id
                task.slots_json = slots
                task.version += 1
                self._event(db, task, "slots_merged", changed, message_id)
            elif task is None or decision.intent not in _WRITE_INTENTS:
                return {"graph_input": decision.model_dump(exclude_none=True), "task": None}

            assert task is not None
            slots = dict(task.slots_json)
            if task.active_case_id:
                slots["case_id"] = task.active_case_id
            missing = [name for name in self._required(task.intent) if not slots.get(name)]
            task.slots_json, task.missing_slots = slots, missing
            if missing:
                task.phase = "awaiting_pickup_slot" if task.intent == "schedule_pickup" else "collecting_slots"
                task.version += 1
                self._event(db, task, "clarification_requested", {"missing_slots": missing}, message_id)
                db.commit()
                return {"reply": self._clarification(task), "task": self._projection(task)}
            task.phase = "ready_to_execute"
            task.version += 1
            self._event(db, task, "task_resumed", {"slots": slots}, message_id)
            db.commit()
            return {"graph_input": {"intent": task.intent, **slots}, "task": self._projection(task)}

    def finish_execution(self, thread_id: str, message_id: str, run_id: str, result: dict) -> tuple[dict, dict | None]:
        """Project graph output back into explicit memory and make recoverable errors non-terminal."""
        with self.session_factory() as db:
            task = db.scalar(select(AgentTask).where(AgentTask.thread_id == thread_id).with_for_update())
            if task is None:
                return result, None
            slots = dict(task.slots_json)
            if result.get("case_id"):
                task.active_case_id = result["case_id"]
                slots["case_id"] = result["case_id"]
            if result.get("status") == "awaiting_confirmation":
                task.phase, task.missing_slots = "awaiting_customer_confirmation", []
                event = "confirmation_requested"
            elif result.get("last_error_code") == "ORDER_NOT_FOUND":
                bad_order = slots.pop("order_id", None)
                task.phase, task.missing_slots = "collecting_slots", ["order_id"]
                result = {**result, "response": f"未找到 {bad_order}，请确认订单号。"}
                event = "slot_validation_failed"
            else:
                task.phase, task.missing_slots = "completed", []
                event = "task_completed"
            task.slots_json = slots
            task.version += 1
            self._event(db, task, event, {"run_id": run_id, "phase": task.phase}, message_id)
            db.commit()
            return result, self._projection(task)

    def finish_confirmation(self, thread_id: str, actor_id: str, approved: bool, case_id: int | None) -> dict | None:
        with self.session_factory() as db:
            task = db.scalar(select(AgentTask).join(AgentThread).where(AgentTask.thread_id == thread_id, AgentThread.actor_id == actor_id).with_for_update())
            if task is None:
                return None
            if approved and case_id is not None and task.intent == "create_after_sales":
                task.intent, task.phase, task.missing_slots, task.active_case_id = "schedule_pickup", "awaiting_pickup_slot", ["time_slot"], case_id
                slots = dict(task.slots_json); slots["case_id"] = case_id; slots["time_slot"] = None; task.slots_json = slots
                event = "confirmation_approved"
            else:
                task.phase, task.missing_slots = ("cancelled", []) if not approved else ("completed", [])
                event = "confirmation_resolved"
            task.version += 1
            self._event(db, task, event, {"approved": approved, "case_id": case_id}, None)
            db.commit()
            return self._projection(task)

    def snapshot(self, thread_id: str, actor_id: str) -> dict:
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id:
                raise ThreadAccessError("会话不存在或不属于当前客户。")
            messages = list(db.scalars(select(AgentMessageRecord).where(AgentMessageRecord.thread_id == thread_id).order_by(AgentMessageRecord.sequence_no)))
            task = db.scalar(select(AgentTask).where(AgentTask.thread_id == thread_id))
            return {
                "thread_id": thread_id,
                "messages": [{"id": row.id, "role": row.role, "content": row.content, "payload": row.payload_json} for row in messages],
                "task": self._projection(task),
            }

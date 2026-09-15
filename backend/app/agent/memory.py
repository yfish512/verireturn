"""Durable, explicit task memory for customer conversations.

LangGraph checkpoints resume an interrupted graph.  This module owns the
customer-facing task: its slots, lifecycle and append-only history.
"""
from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock, RLock
from typing import Any, Callable, Iterator
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AgentConfirmation, AgentMessageRecord, AgentTask, AgentTaskEvent, AgentThread
from .schemas import IntentDecision

_WRITE_INTENTS = {"create_after_sales", "request_manual_review"}
_SLOT_LABELS = {"order_id": "订单号", "request_type": "售后类型", "case_id": "售后单编号", "time_slot": "取件时段"}
_LOCAL_LOCK_GUARD = Lock()
_LOCAL_THREAD_LOCKS: dict[str, RLock] = {}


class ThreadAccessError(Exception):
    pass


class TaskMemory:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    @staticmethod
    def _lock_key(thread_id: str) -> int:
        return int.from_bytes(hashlib.blake2b(thread_id.encode(), digest_size=8).digest(), "big", signed=True)

    @contextmanager
    def execution_lock(self, thread_id: str) -> Iterator[None]:
        """Serialize commands for one thread across API workers.

        PostgreSQL uses a session-level advisory lock held through the full
        agent turn. SQLite tests use a process-local equivalent.
        """
        with self.session_factory() as db:
            if db.get_bind().dialect.name == "postgresql":
                key = self._lock_key(thread_id)
                db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
                try:
                    yield
                finally:
                    db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    db.commit()
                return
        with _LOCAL_LOCK_GUARD:
            lock = _LOCAL_THREAD_LOCKS.setdefault(thread_id, RLock())
        with lock:
            yield

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
        found = re.search(r"(?<![A-Z0-9])(O\d+)(?![A-Z0-9])", message, flags=re.IGNORECASE)
        return found.group(1).upper() if found else None

    @staticmethod
    def _required(intent: str) -> list[str]:
        if intent in _WRITE_INTENTS:
            return ["order_id", "request_type"]
        if intent == "schedule_pickup":
            return ["case_id", "time_slot"]
        return []

    @staticmethod
    def _focused_task(db: Session, thread: AgentThread, *, lock: bool = False) -> AgentTask | None:
        statement = select(AgentTask).where(AgentTask.thread_id == thread.id)
        if thread.focus_task_id:
            statement = statement.where(AgentTask.id == thread.focus_task_id)
        else:
            statement = statement.where(AgentTask.archived_at.is_(None)).order_by(AgentTask.updated_at.desc())
        if lock:
            statement = statement.with_for_update()
        task = db.scalar(statement)
        if task is not None and thread.focus_task_id is None:
            thread.focus_task_id = task.id
        return task

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
            task = self._focused_task(db, thread, lock=True)
            raw_order = self._extract_order(message)
            active_tasks = list(db.scalars(select(AgentTask).where(
                AgentTask.thread_id == thread_id, AgentTask.archived_at.is_(None),
                AgentTask.phase.notin_(("completed", "cancelled", "expired")),
            )))
            if len(active_tasks) > 1 and decision.intent in (_WRITE_INTENTS | {"schedule_pickup"}) and not (raw_order or decision.order_id or decision.case_id):
                choices = "、".join(f"{item.intent}（任务 {item.id[:8]}）" for item in active_tasks[:3])
                return {"reply": f"当前有多个进行中的任务：{choices}。请提供订单号或先在任务列表中切换。", "task": self._projection(task)}
            is_order_correction = bool(
                task and task.intent == "create_after_sales" and task.active_case_id is None
                and task.phase == "completed" and raw_order is not None
            )
            is_slot_reply = bool(task and (
                is_order_correction
                or (task.phase == "awaiting_pickup_slot" and decision.intent not in _WRITE_INTENTS)
                or (task.phase == "collecting_slots" and (
                    raw_order is not None or decision.time_slot is not None or (decision.request_type is not None and decision.intent not in _WRITE_INTENTS)
                ))
            ))
            starts_new_task = decision.intent in _WRITE_INTENTS or decision.intent == "schedule_pickup"
            # Repeated incomplete wording continues the focused task; a ready/other task starts a separate recoverable task.
            if task is not None and task.phase == "collecting_slots" and task.intent == decision.intent and raw_order is None:
                is_slot_reply = True
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
                    thread.focus_task_id = task.id
                    self._event(db, task, "task_created", {"intent": decision.intent, "slots": slots}, message_id)
                else:
                    # Preserve the old task as recoverable history and focus the new request.
                    task = AgentTask(id=str(uuid4()), thread_id=thread_id, intent=decision.intent, phase="collecting_slots", slots_json=slots, missing_slots=[], version=1)
                    db.add(task)
                    thread.focus_task_id = task.id
                    self._event(db, task, "task_created", {"intent": decision.intent, "slots": slots}, message_id)
            elif task is not None and (task.phase in {"collecting_slots", "awaiting_pickup_slot"} or is_order_correction):
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
            thread = db.get(AgentThread, thread_id)
            task = self._focused_task(db, thread, lock=True) if thread is not None else None
            if task is None:
                return result, None
            slots = dict(task.slots_json)
            if result.get("case_id"):
                task.active_case_id = result["case_id"]
                slots["case_id"] = result["case_id"]
            if result.get("status") == "awaiting_confirmation":
                task.phase, task.missing_slots = "awaiting_customer_confirmation", []
                event = "confirmation_requested"
            elif result.get("last_error_code") in {"ORDER_NOT_FOUND", "REFUND_NOT_ELIGIBLE", "EXCHANGE_NOT_ELIGIBLE", "PICKUP_SLOT_INVALID", "PICKUP_SLOT_IN_PAST", "PICKUP_SLOT_OUT_OF_RANGE"}:
                code = result.get("last_error_code")
                if code.startswith("PICKUP_SLOT_"):
                    slots["time_slot"] = None
                    task.phase, task.missing_slots = "awaiting_pickup_slot", ["time_slot"]
                    response = "该取件时段无效或已过期。请提供今天稍后的具体时间，或未来 7 天内的时段，例如“明天上午”。"
                else:
                    previous_order = slots.pop("order_id", None)
                    task.phase, task.missing_slots = "collecting_slots", ["order_id"]
                    response = f"未找到 {previous_order}，请确认订单号。" if code == "ORDER_NOT_FOUND" else "该订单暂不符合售后条件。如订单号有误，请提供正确订单号。"
                result = {**result, "response": response}
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
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id:
                raise ThreadAccessError("会话不存在或不属于当前客户。")
            task = self._focused_task(db, thread, lock=True)
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

    def intent_context(self, thread_id: str, actor_id: str, *, exclude_message_id: str | None = None, limit: int = 6) -> dict | None:
        """Return a bounded, customer-owned context for ambiguous model routing.

        Database task slots stay authoritative.  Transcript text only helps a
        model resolve short references such as “那个订单” or “我刚才说错了”.
        """
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id:
                raise ThreadAccessError("会话不存在或不属于当前客户。")
            task = self._focused_task(db, thread)
            statement = select(AgentMessageRecord).where(AgentMessageRecord.thread_id == thread_id)
            if exclude_message_id:
                statement = statement.where(AgentMessageRecord.id != exclude_message_id)
            rows = list(db.scalars(statement.order_by(AgentMessageRecord.sequence_no.desc()).limit(limit)))
            rows.reverse()
            recent_turns = [
                {"role": row.role, "content": " ".join(row.content.split())[:320]}
                for row in rows
            ]
            active_task = self._projection(task)
            if active_task is not None:
                slots = {
                    key: (" ".join(value.split())[:320] if isinstance(value, str) else value)
                    for key, value in active_task["slots"].items() if value is not None
                }
                active_task = {**active_task, "slots": slots}
            if active_task is None and not recent_turns:
                return None
            return {"active_task": active_task, "recent_turns": recent_turns}

    def cancel_task(self, thread_id: str, actor_id: str) -> dict | None:
        """Stop an unfinished local task without changing confirmed business state."""
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id:
                raise ThreadAccessError("会话不存在或不属于当前客户。")
            task = self._focused_task(db, thread, lock=True)
            if task is None or task.phase in {"completed", "cancelled", "expired"}:
                return None
            if task.phase == "awaiting_customer_confirmation":
                # The runtime must reject the durable confirmation command so
                # the pending business draft is cancelled too.
                return self._projection(task)
            task.phase, task.missing_slots = "cancelled", []
            task.version += 1
            self._event(db, task, "task_cancelled_by_customer", {}, None)
            db.commit()
            return self._projection(task)

    def append_control_reply(self, thread_id: str, response: dict) -> None:
        """Persist confirmation/cancel controls which have no customer message to reply to."""
        with self.session_factory() as db:
            sequence = self._next_sequence(db, AgentMessageRecord, AgentMessageRecord.sequence_no, AgentMessageRecord.thread_id, thread_id)
            db.add(AgentMessageRecord(id=str(uuid4()), thread_id=thread_id, sequence_no=sequence, role="agent", content=response["response"], run_id=response.get("run_id"), payload_json=response))
            thread = db.get(AgentThread, thread_id)
            if thread:
                thread.memory_version += 1; thread.last_active_at = datetime.now(timezone.utc)
            db.commit()

    def list_tasks(self, thread_id: str, actor_id: str, include_archived: bool = True) -> list[dict]:
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id: raise ThreadAccessError("会话不存在或不属于当前客户。")
            statement = select(AgentTask).where(AgentTask.thread_id == thread_id)
            if not include_archived: statement = statement.where(AgentTask.archived_at.is_(None))
            return [self._projection(item) for item in db.scalars(statement.order_by(AgentTask.updated_at.desc()))]

    def focus_task(self, thread_id: str, actor_id: str, task_id: str, *, restore: bool = False) -> dict:
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id: raise ThreadAccessError("会话不存在或不属于当前客户。")
            task = db.scalar(select(AgentTask).where(AgentTask.id == task_id, AgentTask.thread_id == thread_id).with_for_update())
            if task is None: raise ThreadAccessError("任务不存在或不属于当前会话。")
            if restore and task.archived_at is not None: task.archived_at = None; task.version += 1; self._event(db, task, "task_restored", {}, None)
            thread.focus_task_id = task.id; thread.memory_version += 1; db.commit(); return self._projection(task)

    def archive_task(self, thread_id: str, actor_id: str, task_id: str) -> dict:
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id: raise ThreadAccessError("会话不存在或不属于当前客户。")
            task = db.scalar(select(AgentTask).where(AgentTask.id == task_id, AgentTask.thread_id == thread_id).with_for_update())
            if task is None: raise ThreadAccessError("任务不存在或不属于当前会话。")
            task.archived_at = datetime.now(timezone.utc); task.version += 1; self._event(db, task, "task_archived", {}, None)
            if thread.focus_task_id == task.id: thread.focus_task_id = None
            db.commit(); return self._projection(task)

    def reconcile_confirmed_after_sales(self) -> int:
        """Repair task projection after a crash between business confirmation and memory update."""
        with self.session_factory() as db:
            tasks = list(db.scalars(
                select(AgentTask)
                .join(AgentConfirmation, AgentConfirmation.thread_id == AgentTask.thread_id)
                .where(
                    AgentTask.intent == "create_after_sales",
                    AgentTask.phase == "awaiting_customer_confirmation",
                    AgentConfirmation.status == "approved",
                )
                .with_for_update()
            ))
            repaired = 0
            for task in tasks:
                confirmation = db.scalar(
                    select(AgentConfirmation).where(
                        AgentConfirmation.thread_id == task.thread_id,
                        AgentConfirmation.status == "approved",
                    ).order_by(AgentConfirmation.resolved_at.desc())
                )
                if confirmation is None:
                    continue
                slots = dict(task.slots_json)
                slots["case_id"], slots["time_slot"] = confirmation.case_id, None
                task.intent = "schedule_pickup"
                task.phase = "awaiting_pickup_slot"
                task.missing_slots = ["time_slot"]
                task.active_case_id = confirmation.case_id
                task.slots_json = slots
                task.version += 1
                self._event(db, task, "task_projection_recovered", {"confirmation_id": confirmation.id, "case_id": confirmation.case_id}, None)
                repaired += 1
            if repaired:
                db.commit()
            return repaired

    def snapshot(self, thread_id: str, actor_id: str, *, before_sequence: int | None = None, limit: int = 30) -> dict:
        with self.session_factory() as db:
            thread = db.get(AgentThread, thread_id)
            if thread is None or thread.actor_id != actor_id:
                raise ThreadAccessError("会话不存在或不属于当前客户。")
            statement = select(AgentMessageRecord).where(AgentMessageRecord.thread_id == thread_id)
            if before_sequence is not None:
                statement = statement.where(AgentMessageRecord.sequence_no < before_sequence)
            newest_first = list(db.scalars(statement.order_by(AgentMessageRecord.sequence_no.desc()).limit(limit + 1)))
            has_more = len(newest_first) > limit
            rows = newest_first[:limit]
            rows.reverse()
            task = self._focused_task(db, thread)
            tasks = list(db.scalars(select(AgentTask).where(AgentTask.thread_id == thread_id).order_by(AgentTask.updated_at.desc())))
            return {
                "thread_id": thread_id,
                "messages": [{"id": row.id, "role": row.role, "content": row.content, "payload": row.payload_json} for row in rows],
                "task": self._projection(task),
                "tasks": [self._projection(item) for item in tasks],
                "next_before_sequence": rows[0].sequence_no if has_more and rows else None,
            }

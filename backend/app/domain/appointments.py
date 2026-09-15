"""Structured, capacity-aware reverse-logistics appointments."""
from __future__ import annotations
import re
from datetime import datetime, date, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ..models import AfterSalesCase, PickupAppointment, PickupSlotCapacity
from .service import DomainError, _audit, _validate_pickup_slot

TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_CAPACITY = 20


def _window(text: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    value = _validate_pickup_slot(text, now)
    local_now = (now or datetime.now(timezone.utc)).astimezone(TZ)
    if "明天" in value: day = local_now.date() + timedelta(days=1)
    elif "后天" in value: day = local_now.date() + timedelta(days=2)
    elif "今天" in value: day = local_now.date()
    else:
        hit = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", value)
        assert hit is not None
        day = date(int(hit.group(1)), int(hit.group(2)), int(hit.group(3)))
    hour = re.search(r"(?<!\d)([01]?\d|2[0-3])(?:点|:)(\d{1,2})?", value)
    if hour:
        start = datetime.combine(day, time(int(hour.group(1)), int(hour.group(2) or 0)), TZ)
        end = start + timedelta(hours=2)
    elif "上午" in value:
        start, end = datetime.combine(day, time(9), TZ), datetime.combine(day, time(12), TZ)
    elif "下午" in value:
        start, end = datetime.combine(day, time(13), TZ), datetime.combine(day, time(18), TZ)
    elif "晚上" in value:
        start, end = datetime.combine(day, time(18), TZ), datetime.combine(day, time(20), TZ)
    else:
        # A date alone is valid only as an explicit default service window.
        start, end = datetime.combine(day, time(9), TZ), datetime.combine(day, time(12), TZ)
    if start <= local_now:
        raise DomainError("PICKUP_SLOT_IN_PAST", "取件开始时间已过去，请选择未来时段。", 422)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), value


def reserve_appointment(db: Session, case: AfterSalesCase, text: str, now: datetime | None = None) -> PickupAppointment:
    start, end, display = _window(text, now)
    existing = db.scalar(select(PickupAppointment).where(PickupAppointment.case_id == case.id).with_for_update())
    if existing is not None:
        if existing.start_at == start and existing.end_at == end and existing.status == "scheduled": return existing
        raise DomainError("PICKUP_APPOINTMENT_EXISTS", "该售后单已有预约，不能覆盖。", 409)
    capacity = db.scalar(select(PickupSlotCapacity).where(PickupSlotCapacity.provider == "demo_fulfillment", PickupSlotCapacity.start_at == start, PickupSlotCapacity.end_at == end).with_for_update())
    if capacity is None:
        capacity = PickupSlotCapacity(id=str(uuid4()), provider="demo_fulfillment", start_at=start, end_at=end, capacity=DEFAULT_CAPACITY, reserved_quantity=0)
        db.add(capacity); db.flush()
    if capacity.reserved_quantity >= capacity.capacity:
        raise DomainError("PICKUP_SLOT_FULL", "该取件时段已约满，请选择其他时段。", 409)
    capacity.reserved_quantity += 1; capacity.version += 1
    appointment = PickupAppointment(id=str(uuid4()), case_id=case.id, start_at=start, end_at=end, display_text=display)
    db.add(appointment)
    _audit(db, case.id, "PICKUP_APPOINTMENT_RESERVED", f"预约窗口：{start.isoformat()} 至 {end.isoformat()}。", "system", "pickup-scheduler", appointment.id)
    return appointment


def cancel_appointment(db: Session, case: AfterSalesCase, reason: str) -> bool:
    appointment = db.scalar(select(PickupAppointment).where(PickupAppointment.case_id == case.id).with_for_update())
    if appointment is None or appointment.status != "scheduled": return False
    capacity = db.scalar(select(PickupSlotCapacity).where(PickupSlotCapacity.provider == appointment.provider, PickupSlotCapacity.start_at == appointment.start_at, PickupSlotCapacity.end_at == appointment.end_at).with_for_update())
    if capacity is not None:
        capacity.reserved_quantity = max(0, capacity.reserved_quantity - 1); capacity.version += 1
    appointment.status, appointment.version = "cancelled", appointment.version + 1
    _audit(db, case.id, "PICKUP_APPOINTMENT_CANCELLED", reason, "system", "pickup-scheduler", appointment.id)
    return True


def list_available_slots(db: Session, now: datetime | None = None, days: int = 7) -> list[dict]:
    local_now = (now or datetime.now(timezone.utc)).astimezone(TZ)
    rows=[]
    for offset in range(days + 1):
        day = local_now.date() + timedelta(days=offset)
        for label, start_hour, end_hour in (("上午",9,12),("下午",13,18)):
            start = datetime.combine(day, time(start_hour), TZ).astimezone(timezone.utc); end=datetime.combine(day,time(end_hour),TZ).astimezone(timezone.utc)
            if start <= local_now.astimezone(timezone.utc): continue
            cap = db.scalar(select(PickupSlotCapacity).where(PickupSlotCapacity.provider=="demo_fulfillment", PickupSlotCapacity.start_at==start, PickupSlotCapacity.end_at==end))
            capacity, reserved = (cap.capacity, cap.reserved_quantity) if cap else (DEFAULT_CAPACITY,0)
            rows.append({"start_at":start,"end_at":end,"timezone":"Asia/Shanghai","label":f"{day.isoformat()} {label}","available":max(0,capacity-reserved)})
    return rows

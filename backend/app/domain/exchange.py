"""Inventory-backed exchange fulfillment. All counters are changed under stock row locks."""
from __future__ import annotations
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import AfterSalesCase, AfterSalesItem, ExchangeFulfillment, FulfillmentIncident, InventoryReservation, InventoryStock, OrderItem
from .service import _audit


def _incident(db: Session, case: AfterSalesCase, code: str, detail: str) -> None:
    if db.scalar(select(FulfillmentIncident).where(FulfillmentIncident.case_id == case.id, FulfillmentIncident.dedupe_key == f"exchange:{code}")) is None:
        db.add(FulfillmentIncident(id=str(uuid4()), case_id=case.id, incident_type=code, detail=detail, dedupe_key=f"exchange:{code}"))


def reserve_exchange(db: Session, case: AfterSalesCase) -> ExchangeFulfillment | None:
    """Reserve a replacement at confirmation. Stock shortage is an operational fact, never a fake shipment."""
    if case.request_type != "exchange": return None
    existing = db.scalar(select(ExchangeFulfillment).where(ExchangeFulfillment.case_id == case.id).with_for_update())
    if existing is not None: return existing
    lines = list(db.scalars(select(AfterSalesItem).where(AfterSalesItem.case_id == case.id)))
    if len(lines) != 1:
        case.status = "fulfillment_exception"; _incident(db, case, "EXCHANGE_MULTI_LINE_REQUIRES_OPS", "多商品换货需运营确认替换规格。")
        _audit(db, case.id, "EXCHANGE_STOCK_EXCEPTION", "多商品换货等待运营处理。", "system", "inventory", None); return None
    item = db.scalar(select(OrderItem).where(OrderItem.id == lines[0].order_item_id))
    assert item is not None
    stock = db.scalar(select(InventoryStock).where(InventoryStock.sku == item.sku).with_for_update())
    if stock is None or stock.available_quantity - stock.reserved_quantity < lines[0].quantity:
        case.status = "fulfillment_exception"; _incident(db, case, "EXCHANGE_OUT_OF_STOCK", "替换商品库存不足，等待补货、退款或人工处理。")
        _audit(db, case.id, "EXCHANGE_STOCK_EXCEPTION", "替换商品库存不足。", "system", "inventory", None); return None
    reservation = InventoryReservation(id=str(uuid4()), case_id=case.id, sku=item.sku, quantity=lines[0].quantity, status="reserved")
    stock.reserved_quantity += reservation.quantity; stock.version += 1
    fulfillment = ExchangeFulfillment(id=str(uuid4()), case_id=case.id, replacement_sku=item.sku, quantity=reservation.quantity, reservation_id=reservation.id, status="allocated")
    db.add_all([reservation, fulfillment]); _audit(db, case.id, "EXCHANGE_INVENTORY_RESERVED", f"已预占 {item.sku} x{reservation.quantity}。", "system", "inventory", reservation.id)
    return fulfillment


def release_exchange_reservation(db: Session, case: AfterSalesCase, reason: str) -> bool:
    reservation = db.scalar(select(InventoryReservation).where(InventoryReservation.case_id == case.id).with_for_update())
    if reservation is None or reservation.status != "reserved": return False
    stock = db.scalar(select(InventoryStock).where(InventoryStock.sku == reservation.sku).with_for_update())
    if stock is not None:
        stock.reserved_quantity = max(0, stock.reserved_quantity - reservation.quantity); stock.version += 1
    reservation.status = "released"
    fulfillment = db.scalar(select(ExchangeFulfillment).where(ExchangeFulfillment.case_id == case.id).with_for_update())
    if fulfillment is not None: fulfillment.status, fulfillment.failure_code, fulfillment.version = "cancelled", reason, fulfillment.version + 1
    _audit(db, case.id, "EXCHANGE_RESERVATION_RELEASED", reason, "system", "inventory", reservation.id)
    return True


def consume_exchange_reservation(db: Session, case: AfterSalesCase, tracking_number: str | None = None) -> ExchangeFulfillment | None:
    reservation = db.scalar(select(InventoryReservation).where(InventoryReservation.case_id == case.id).with_for_update())
    fulfillment = db.scalar(select(ExchangeFulfillment).where(ExchangeFulfillment.case_id == case.id).with_for_update())
    if reservation is None or fulfillment is None: return None
    if reservation.status == "consumed": return fulfillment
    if reservation.status != "reserved": return None
    stock = db.scalar(select(InventoryStock).where(InventoryStock.sku == reservation.sku).with_for_update())
    if stock is None or stock.available_quantity < reservation.quantity:
        case.status = "fulfillment_exception"; fulfillment.status, fulfillment.failure_code = "failed", "STOCK_INCONSISTENCY"; _incident(db, case, "STOCK_INCONSISTENCY", "预占库存无法出库。")
        return fulfillment
    stock.reserved_quantity = max(0, stock.reserved_quantity - reservation.quantity); stock.available_quantity -= reservation.quantity; stock.version += 1
    reservation.status = "consumed"; fulfillment.status, fulfillment.tracking_number, fulfillment.version = "shipped", tracking_number, fulfillment.version + 1
    _audit(db, case.id, "EXCHANGE_REPLACEMENT_SHIPPED", "替换商品已出库。", "system", "inventory", fulfillment.id)
    return fulfillment

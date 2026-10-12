"""Customer-authorized resolution commands; every mutation is one transaction."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, func
from ..models import (AfterSalesCase, AfterSalesItem, AfterSalesResolution, InventoryStock,
                      OrderItem, ReplacementOption, InventoryReservation, ExchangeFulfillment)
from .service import DomainError, _audit, request_fingerprint
from .exchange import release_exchange_reservation, reserve_exchange
from .fulfillment import _create_incident


def snapshot(db, case):
    lines = list(db.scalars(select(AfterSalesItem).where(AfterSalesItem.case_id == case.id)))
    skus = [db.get(OrderItem, line.order_item_id).sku for line in lines]
    return {"case_status": case.status, "case_updated_at": case.updated_at.isoformat(),
            "lines": [{"id": l.order_item_id, "quantity": l.quantity} for l in lines],
            "stock": {sku: db.get(InventoryStock, sku).version if db.get(InventoryStock, sku) else None for sku in skus}}


def owned_locked(db, actor, case_id):
    case = db.scalar(select(AfterSalesCase).where(AfterSalesCase.id == case_id).with_for_update())
    if not case or case.user_id != actor:
        raise DomainError("CASE_NOT_FOUND", "售后单不存在。", 404)
    return case


def resolution_options(db, actor, case_id):
    case = owned_locked(db, actor, case_id)
    if case.status != "fulfillment_exception" or case.request_type != "exchange":
        raise DomainError("RESOLUTION_NOT_ALLOWED", "当前售后单无需换货异常处理。", 409)
    lines = list(db.scalars(select(AfterSalesItem).where(AfterSalesItem.case_id == case.id)))
    from .policy import evaluate_after_sales
    from ..models import Order
    existing_fulfillment = db.scalar(select(ExchangeFulfillment).where(ExchangeFulfillment.case_id == case.id))
    if existing_fulfillment and existing_fulfillment.status in {"shipped", "delivered"}:
        return {"case_id": case.id, "options": [{"option": "human"}], "snapshot": snapshot(db, case)}
    refund_eligible = evaluate_after_sales(db.get(Order, case.order_id), "refund").eligible
    options = ([{"option": "refund"}] if refund_eligible else []) + [{"option": "wait"}, {"option": "human"}]
    if len(lines) == 1:
        sku = db.get(OrderItem, lines[0].order_item_id).sku
        candidates = [sku] + list(db.scalars(select(ReplacementOption.replacement_sku).where(ReplacementOption.original_sku == sku, ReplacementOption.active.is_(True))))
        for candidate in candidates:
            stock = db.get(InventoryStock, candidate)
            if stock and stock.available_quantity - stock.reserved_quantity >= lines[0].quantity:
                options.append({"option": "exchange", "replacement_sku": candidate})
    return {"case_id": case.id, "options": options, "snapshot": snapshot(db, case)}


def propose_resolution(db, actor, case_id, option, key, replacement_sku=None):
    case = owned_locked(db, actor, case_id)
    digest = request_fingerprint({"option": option, "replacement_sku": replacement_sku})
    existing = db.scalar(select(AfterSalesResolution).where(AfterSalesResolution.case_id == case_id, AfterSalesResolution.idempotency_key == key))
    if existing:
        if existing.fingerprint != digest:
            raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "相同幂等键不能更改方案。", 409)
        return existing
    choices = resolution_options(db, actor, case_id)["options"]
    choice = {"option": option}
    if replacement_sku: choice["replacement_sku"] = replacement_sku
    if choice not in choices:
        raise DomainError("RESOLUTION_OPTION_INVALID", "方案不可用，请重新选择。", 422)
    row = AfterSalesResolution(id=str(uuid4()), case_id=case_id, actor_id=actor, option=option,
        replacement_sku=replacement_sku, idempotency_key=key, fingerprint=digest,
        snapshot_json=snapshot(db, case), expires_at=datetime.now(timezone.utc)+timedelta(minutes=15))
    if replacement_sku:
        row.snapshot_json = {**row.snapshot_json, "replacement_version": db.get(InventoryStock, replacement_sku).version}
    db.add(row); _audit(db, case_id, "RESOLUTION_PROPOSED", option, "customer", actor, row.id)
    db.commit(); return row


def confirm_resolution(db, actor, resolution_id, expected_version, approved):
    # Uniform case -> resolution -> lines -> stocks lock order.
    peek = db.get(AfterSalesResolution, resolution_id)
    if not peek or peek.actor_id != actor: raise DomainError("RESOLUTION_NOT_FOUND", "方案不存在。", 404)
    case = owned_locked(db, actor, peek.case_id)
    row = db.scalar(select(AfterSalesResolution).where(AfterSalesResolution.id == resolution_id).with_for_update())
    if row.status != "awaiting_confirmation":
        if row.version == expected_version + 1 and row.status in {"applied", "waiting_stock", "human", "rejected"}:
            if (row.status == "rejected") != (not approved): raise DomainError("RESOLUTION_CONFLICT", "确认结果冲突。", 409)
            return row
        raise DomainError("RESOLUTION_CONFLICT", "方案已处理。", 409)
    if row.version != expected_version: raise DomainError("RESOLUTION_CONFLICT", "方案版本已更新。", 409)
    expiry = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
    if expiry <= datetime.now(timezone.utc): raise DomainError("RESOLUTION_EXPIRED", "方案已过期，请重新选择。", 409)
    if not approved:
        row.status = "rejected"
    else:
        # Lock stock before validating the proposal snapshot, so confirmation cannot
        # cross a concurrent stock update between validation and reservation.
        stock_skus = sorted(set(row.snapshot_json.get("stock", {})) | ({row.replacement_sku} if row.replacement_sku else set()))
        for sku in stock_skus:
            db.scalar(select(InventoryStock).where(InventoryStock.sku == sku).with_for_update())
        current = snapshot(db, case)
        if current != {k:v for k,v in row.snapshot_json.items() if k != "replacement_version"}:
            raise DomainError("RESOLUTION_STALE", "业务状态已变化，请重新确认方案。", 409)
        if row.replacement_sku and db.get(InventoryStock, row.replacement_sku).version != row.snapshot_json["replacement_version"]:
            raise DomainError("RESOLUTION_STALE", "替换库存已变化，请重新选择。", 409)
        if row.option == "refund":
            from .policy import evaluate_after_sales
            from ..models import Order
            if not evaluate_after_sales(db.get(Order, case.order_id), "refund").eligible:
                raise DomainError("REFUND_REQUIRES_REVIEW", "退款需要重新核验资格或人工审核。", 409)
            lines = list(db.scalars(select(AfterSalesItem).where(AfterSalesItem.case_id == case.id)))
            total = Decimal("0")
            for line in lines:
                item = db.scalar(select(OrderItem).where(OrderItem.id == line.order_item_id).with_for_update())
                other = db.scalar(select(func.coalesce(func.sum(AfterSalesItem.quantity), 0)).join(AfterSalesCase).where(
                    AfterSalesItem.order_item_id == item.id, AfterSalesItem.case_id != case.id,
                    AfterSalesCase.status.in_(("awaiting_pickup", "pickup_scheduled", "picked_up", "return_received", "refund_processing", "replacement_shipped", "manual_review", "fulfillment_exception")))) or 0
                if line.quantity + item.refunded_quantity + int(other) > item.quantity:
                    raise DomainError("REFUND_QUANTITY_EXCEEDED", "商品可退款数量不足。", 409)
                total += Decimal(item.unit_amount) * line.quantity
            release_exchange_reservation(db, case, "CONVERT_TO_REFUND")
            case.status = "cancelled"
            linked = AfterSalesCase(user_id=actor, order_id=case.order_id, request_type="refund", reason="客户确认换货转退款",
                status="awaiting_pickup", eligible_amount=total, operation="resolve_exchange_refund", idempotency_key=row.id,
                request_hash=row.fingerprint)
            db.add(linked); db.flush()
            for line in lines:
                db.add(AfterSalesItem(id=str(uuid4()), case_id=linked.id, order_item_id=line.order_item_id,
                    quantity=line.quantity, refund_amount=Decimal(db.get(OrderItem, line.order_item_id).unit_amount)*line.quantity))
            row.linked_case_id, row.status = linked.id, "applied"
            _audit(db, linked.id, "CASE_CREATED_BY_RESOLUTION", f"原售后 #{case.id}", "customer", actor, row.id)
        elif row.option == "exchange":
            release_exchange_reservation(db, case, "REPLACE_ALLOCATION")
            case.status = "awaiting_pickup"
            reserve_exchange(db, case, replacement_sku=row.replacement_sku)
            if case.status == "fulfillment_exception": raise DomainError("RESOLUTION_STALE", "库存不足。", 409)
            row.status = "applied"
        elif row.option == "wait": row.status = "waiting_stock"
        elif row.option == "human":
            _create_incident(db, case.id, "RECOVERY_HUMAN_REQUIRED", "客户选择人工处理。", f"resolution:{row.id}")
            row.status = "human"
    if approved: case.updated_at = datetime.now(timezone.utc)
    row.version += 1
    _audit(db, case.id, "RESOLUTION_CONFIRMED" if approved else "RESOLUTION_REJECTED", row.option, "customer", actor, row.id)
    db.commit(); return row


def refresh_waiting_resolutions(db):
    """Restock offers a new proposal; it never reuses the customer's old consent."""
    rows=list(db.scalars(select(AfterSalesResolution).where(AfterSalesResolution.status=='waiting_stock')))
    refreshed=0
    for old in rows:
        case=db.get(AfterSalesCase,old.case_id)
        if not case or case.status!='fulfillment_exception':continue
        choices=resolution_options(db,old.actor_id,old.case_id)['options']
        exchanges=[c for c in choices if c['option']=='exchange']
        if not exchanges:continue
        new=propose_resolution(db,old.actor_id,old.case_id,'exchange',f'restock:{old.id}',exchanges[0]['replacement_sku'])
        old=db.get(AfterSalesResolution,old.id);old.status='restock_available';old.version+=1
        from .fulfillment import _queue_notification
        _queue_notification(db,case,'recovery_restock','库存已有可用方案，请重新确认换货。',f'restock:{old.id}')
        from ..models import AgentTask
        for task in db.scalars(select(AgentTask).where(AgentTask.active_case_id==old.case_id,AgentTask.phase=='waiting_stock')):
            task.phase='awaiting_resolution';task.version+=1
        db.commit();refreshed+=1
    return refreshed

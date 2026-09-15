"""Acceptance coverage for the P0-P2 reliability additions."""
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from backend.app.agent.memory import TaskMemory
from backend.app.agent.schemas import IntentDecision
from backend.app.database import Base
from backend.app.domain.exchange import release_exchange_reservation
from backend.app.domain.fulfillment import receive_provider_event
from backend.app.domain.payments import apply_refund_settlement, ensure_refund_intent
from backend.app.domain.service import cancel_case, confirm_case, create_case, schedule_pickup
from backend.app.models import AfterSalesCase, ExchangeFulfillment, InventoryReservation, InventoryStock, OrderItem, RefundIntent
from backend.app.schemas import AfterSalesCreateRequest, AfterSalesItemRequest
from backend.app.seed import seed_demo_data


def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p0p2.db'}")
    Base.metadata.create_all(engine); factory=sessionmaker(bind=engine)
    db=factory(); seed_demo_data(db); return db, factory


def test_multiple_task_focus_and_control_reply_survive_snapshot(tmp_path):
    db, factory = db_session(tmp_path); db.close(); memory=TaskMemory(factory); memory.ensure_thread("thread-p0", "U001")
    first=memory.resolve("thread-p0","U001","m1","帮我退款",IntentDecision(intent="create_after_sales",order_id="O1001",request_type="refund"))
    second=memory.resolve("thread-p0","U001","m2","帮我换货 O1003",IntentDecision(intent="create_after_sales",order_id="O1003",request_type="exchange"))
    tasks=memory.list_tasks("thread-p0","U001")
    assert len(tasks)==2 and second["task"]["task_id"] != first["task"]["task_id"]
    memory.focus_task("thread-p0","U001",first["task"]["task_id"])
    memory.append_control_reply("thread-p0", {"response":"已取消本次申请。", "run_id":"run-control"})
    snapshot=memory.snapshot("thread-p0","U001")
    assert snapshot["task"]["task_id"]==first["task"]["task_id"]
    assert snapshot["messages"][-1]["content"]=="已取消本次申请。"


def test_partial_line_refund_settlement_and_exchange_release(tmp_path):
    db, _ = db_session(tmp_path)
    # Make a multi-line order so the compatibility path cannot hide selection logic.
    db.add(OrderItem(id="item-o1001-cable",order_id="O1001",sku="AURORA-CABLE",title="线材",quantity=2,unit_amount=Decimal("10")))
    db.add(InventoryStock(sku="AURORA-CABLE",available_quantity=1,reserved_quantity=0)); db.commit()
    headset=db.scalar(select(OrderItem).where(OrderItem.id=="item-o1001-headset"))
    case=create_case(db,"U001",AfterSalesCreateRequest(order_id="O1001",request_type="refund",reason="部分退款",items=[AfterSalesItemRequest(order_item_id=headset.id,quantity=1)]),"partial-line-refund")
    confirm_case(db,"U001",case.id,"partial-confirm"); schedule_pickup(db,"U001",case.id,"明天上午","partial-pickup")
    for no, kind in ((1,"pickup.collected"),(2,"return.received")):
        receive_provider_event(db,"demo_fulfillment",{"event_id":f"partial-event-{no}","case_id":case.id,"event_type":kind,"sequence_no":no,"occurred_at":datetime.now(timezone.utc),"payload":{}})
    intent=db.scalar(select(RefundIntent).where(RefundIntent.case_id==case.id)); intent.provider_refund_id="partial-provider"; intent.status="submitted"; db.commit()
    apply_refund_settlement(db,"demo_payment","partial-provider",True,"partial-settlement",{})
    assert db.get(OrderItem,headset.id).refunded_quantity==1
    # Exchange reserves at confirmation and cancellation returns stock.
    exchange=create_case(db,"U001",AfterSalesCreateRequest(order_id="O1003",request_type="exchange",reason="质量问题",items=[AfterSalesItemRequest(order_item_id="item-o1003-sport",quantity=1)]),"exchange-reserve")
    confirm_case(db,"U001",exchange.id,"exchange-confirm")
    reservation=db.scalar(select(InventoryReservation).where(InventoryReservation.case_id==exchange.id)); assert reservation and reservation.status=="reserved"
    cancel_case(db,"U001",exchange.id,"exchange-cancel")
    assert db.get(InventoryReservation,reservation.id).status=="released"

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Actor, AlertRuleVersion, EvaluationSuite, InventoryStock, Logistics, Order, OrderItem, PaymentTransaction, PolicyVersion, User
from .domain.service import request_fingerprint


def seed_demo_data(db: Session) -> None:
    now = datetime.now(timezone.utc)
    # Flush the parent rows before dependent logistics rows. SQLite's insertion
    # behavior can hide this ordering issue; PostgreSQL enforces the FK as soon
    # as the INSERT is issued.
    if db.get(User, "U001") is None:
        db.add_all([
            User(id="U001", name="李雷"),
            User(id="U002", name="韩梅梅"),
        ])
        db.flush()
        db.add_all([
            Order(id="O1001", user_id="U001", item_name="Aurora 无线降噪耳机", amount=299, delivered_at=now - timedelta(days=3), condition="sealed", quality_issue=False),
            Order(id="O1002", user_id="U001", item_name="Aurora 蓝牙耳机", amount=199, delivered_at=now - timedelta(days=10), condition="opened", quality_issue=False),
            Order(id="O1003", user_id="U001", item_name="Aurora 运动耳机", amount=399, delivered_at=now - timedelta(days=15), condition="opened", quality_issue=True),
            Order(id="O1004", user_id="U002", item_name="Aurora 入门耳机", amount=129, delivered_at=now - timedelta(days=2), condition="sealed", quality_issue=False),
        ])
        db.flush()
        db.add_all([
            Logistics(order_id="O1001", status="delivered", tracking_number="SF1001001"),
            Logistics(order_id="O1002", status="delivered", tracking_number="SF1001002"),
            Logistics(order_id="O1003", status="delivered", tracking_number="SF1001003"),
            Logistics(order_id="O1004", status="delivered", tracking_number="SF1001004"),
        ])
    # Real line rows make partial-refund and exchange demos deterministic.
    line_specs = [
        ("item-o1001-headset", "O1001", "AURORA-NC", "Aurora 无线降噪耳机", 1, Decimal("299")),
        ("item-o1002-headset", "O1002", "AURORA-BT", "Aurora 蓝牙耳机", 1, Decimal("199")),
        ("item-o1003-sport", "O1003", "AURORA-SPORT", "Aurora 运动耳机", 1, Decimal("399")),
        ("item-o1004-basic", "O1004", "AURORA-BASIC", "Aurora 入门耳机", 1, Decimal("129")),
    ]
    for line_id, order_id, sku, title, quantity, amount in line_specs:
        if db.get(OrderItem, line_id) is None: db.add(OrderItem(id=line_id, order_id=order_id, sku=sku, title=title, quantity=quantity, unit_amount=amount))
        if db.get(InventoryStock, sku) is None: db.add(InventoryStock(sku=sku, available_quantity=20, reserved_quantity=0))
    for order_id, user_id, amount in (("O1001", "U001", Decimal("299")), ("O1002", "U001", Decimal("199")), ("O1003", "U001", Decimal("399")), ("O1004", "U002", Decimal("129"))):
        if db.scalar(select(PaymentTransaction).where(PaymentTransaction.order_id == order_id)) is None:
            db.add(PaymentTransaction(id=f"payment-{order_id}", order_id=order_id, user_id=user_id, provider="demo_payment", provider_payment_id=f"pay-{order_id}", amount=amount, status="captured"))
    for actor in (
        Actor(id="U001", display_name="李雷", role="customer"),
        Actor(id="U002", display_name="韩梅梅", role="customer"),
        Actor(id="OPS001", display_name="运营一号", role="operator"),
        Actor(id="OPS_MANAGER", display_name="运营主管", role="ops_manager"),
        Actor(id="FIN001", display_name="财务一号", role="finance"),
        Actor(id="REVIEW001", display_name="审核一号", role="reviewer"),
        Actor(id="PAYMENT_WORKER", display_name="支付服务", role="internal_service"),
    ):
        if db.get(Actor, actor.id) is None:
            db.add(actor)
    default_rules = {"quality_dispute_enabled": True, "quality_dispute_terms": ["质量", "故障", "损坏", "坏了", "无法使用"], "review_sla_hours": 24}
    if db.get(PolicyVersion, "policy-m3-default-v1") is None:
        db.add(PolicyVersion(
            id="policy-m3-default-v1", version="m3-default-v1", status="published", rules_json=default_rules,
            checksum=request_fingerprint(default_rules), published_by="OPS_MANAGER", published_at=now,
        ))
    default_rules = (
        ("fulfillment-outbox-backlog", "fulfillment.outbox_pending", Decimal("0"), "critical"),
        ("fulfillment-incident-backlog", "fulfillment.incidents_open", Decimal("0"), "warning"),
        ("agent-negative-feedback", "agent.negative_feedback_rate", Decimal("0.20"), "warning"),
    )
    for rule_key, metric_name, threshold, severity in default_rules:
        if db.scalar(select(AlertRuleVersion.id).where(AlertRuleVersion.rule_key == rule_key)) is None:
            db.add(AlertRuleVersion(
                id=f"m6-rule-{rule_key}", rule_key=rule_key, version=1, metric_name=metric_name,
                comparison=">", threshold=threshold, severity=severity, status="published",
                created_by="OPS_MANAGER", published_at=now,
            ))
    project = Path(__file__).resolve().parents[2]
    for suite_key, case_path in (("m2", "evals/cases/m2_agent_cases.jsonl"), ("m3", "evals/cases/m3_agent_cases.jsonl"), ("m4", "evals/cases/m4_knowledge_agent_cases.jsonl"), ("m5", "evals/cases/m5_fulfillment_agent_cases.jsonl")):
        if db.scalar(select(EvaluationSuite.id).where(EvaluationSuite.suite_key == suite_key, EvaluationSuite.version == 1)) is None:
            content = (project / case_path).read_bytes()
            db.add(EvaluationSuite(id=f"m6-suite-{suite_key}-v1", suite_key=suite_key, version=1, case_path=case_path, checksum=hashlib.sha256(content).hexdigest(), created_by="OPS_MANAGER"))
    db.commit()

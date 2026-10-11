"""Real domain-service scenarios used by the Recovery LangGraph experiment."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from ..database import Base
from ..domain.payments import ensure_refund_intent, submit_refund_provider
from ..domain.service import confirm_case, create_case, schedule_pickup
from ..models import AfterSalesCase, InventoryStock, RefundAttempt, RefundIntent
from ..schemas import AfterSalesCreateRequest
from ..seed import seed_demo_data
from .contracts import FaultRule
from .fault_injector import FaultInjector

SCENARIOS = {
    "inventory_temporarily_unavailable": {
        "title": "库存查询临时不可用",
        "fault": ("inventory_check", "temporary_503"),
        "user_goal": "确认换货前查询替换商品库存。",
    },
    "refund_outcome_unknown": {
        "title": "退款提交后结果未知",
        "fault": ("submit_refund", "timeout_after_commit"),
        "user_goal": "提交退款，不得重复向支付渠道发起命令。",
    },
    "inventory_changed_during_confirmation": {
        "title": "确认期间库存被占用",
        "fault": ("confirm_exchange", "state_change_before_call"),
        "user_goal": "确认换货；缺货时必须重新给出可确认的替代方案。",
    },
}

@dataclass
class ScenarioContext:
    case_id: int | None = None
    refund_intent_id: str | None = None

class ScenarioHarness:
    """Executes a scenario against the existing transactional domain services."""
    def __init__(self, scenario_id: str, seed: int = 0, *, session_factory: Callable[[], Session] | None = None):
        if scenario_id not in SCENARIOS:
            raise ValueError(f"unknown recovery scenario: {scenario_id}")
        self.scenario_id, self.seed = scenario_id, seed
        if session_factory is None:
            engine = create_engine("sqlite:///:memory:")
            Base.metadata.create_all(engine)
            self.session_factory = sessionmaker(bind=engine)
            with self.session_factory() as db:
                seed_demo_data(db)
        else:
            self.session_factory = session_factory
        tool, action = SCENARIOS[scenario_id]["fault"]
        self.injector = FaultInjector(FaultRule(scenario_id, tool, action, seed=seed))
        self.context = ScenarioContext()
        self.primary_calls = 0
        self.verify_calls = 0
        self._prepare()

    def _prepare(self) -> None:
        if self.scenario_id == "inventory_temporarily_unavailable":
            return
        with self.session_factory() as db:
            request_type = "refund" if self.scenario_id == "refund_outcome_unknown" else "exchange"
            case = create_case(db, "U001", AfterSalesCreateRequest(order_id="O1001" if request_type == "refund" else "O1003", request_type=request_type, reason="Recovery 实验"), f"recovery-create-{self.seed}-{self.scenario_id}")
            self.context.case_id = case.id
            if request_type == "refund":
                confirm_case(db, "U001", case.id, f"recovery-confirm-{self.seed}")
                schedule_pickup(db, "U001", case.id, "明天上午", f"recovery-pickup-{self.seed}")
                intent = ensure_refund_intent(db, case)
                db.commit()
                self.context.refund_intent_id = intent.id

    def _inventory(self) -> dict:
        with self.session_factory() as db:
            stock = db.get(InventoryStock, "AURORA-SPORT")
            assert stock is not None
            return {"sku": stock.sku, "available": stock.available_quantity - stock.reserved_quantity, "version": stock.version}

    def _submit_refund(self) -> dict:
        assert self.context.refund_intent_id
        with self.session_factory() as db:
            intent = submit_refund_provider(db, self.context.refund_intent_id)
            return {"intent_id": intent.id, "status": intent.status, "provider_refund_id": intent.provider_refund_id}

    def _occupy_last_stock(self) -> None:
        with self.session_factory() as db:
            stock = db.get(InventoryStock, "AURORA-SPORT")
            assert stock is not None
            stock.available_quantity = stock.reserved_quantity
            stock.version += 1
            db.commit()

    def _confirm_exchange(self) -> dict:
        assert self.context.case_id
        with self.session_factory() as db:
            case = confirm_case(db, "U001", self.context.case_id, f"recovery-exchange-confirm-{self.seed}")
            return {"case_id": case.id, "status": case.status}

    def execute_primary(self) -> dict:
        self.primary_calls += 1
        if self.scenario_id == "inventory_temporarily_unavailable":
            return self.injector.call("inventory_check", self._inventory)
        if self.scenario_id == "refund_outcome_unknown":
            return self.injector.call("submit_refund", self._submit_refund)
        return self.injector.call("confirm_exchange", self._confirm_exchange, before=self._occupy_last_stock)

    def verify(self) -> dict:
        self.verify_calls += 1
        with self.session_factory() as db:
            if self.scenario_id == "refund_outcome_unknown":
                assert self.context.refund_intent_id
                intent = db.get(RefundIntent, self.context.refund_intent_id)
                assert intent is not None
                attempts = int(db.scalar(select(func.count()).select_from(RefundAttempt).where(RefundAttempt.refund_intent_id == intent.id)) or 0)
                return {"intent_status": intent.status, "provider_refund_id": intent.provider_refund_id, "refund_attempts": attempts}
            if self.scenario_id == "inventory_changed_during_confirmation":
                assert self.context.case_id
                case = db.get(AfterSalesCase, self.context.case_id)
                stock = db.get(InventoryStock, "AURORA-SPORT")
                assert case and stock
                return {"case_status": case.status, "available": stock.available_quantity - stock.reserved_quantity}
        return self._inventory()

    def final_state(self) -> dict:
        with self.session_factory() as db:
            if self.scenario_id == "refund_outcome_unknown":
                assert self.context.refund_intent_id
                intent = db.get(RefundIntent, self.context.refund_intent_id)
                attempts = int(db.scalar(select(func.count()).select_from(RefundAttempt).where(RefundAttempt.refund_intent_id == intent.id)) or 0)
                return {"refund_intent_status": intent.status if intent else None, "provider_refund_id": intent.provider_refund_id if intent else None, "refund_attempts": attempts}
            if self.scenario_id == "inventory_changed_during_confirmation":
                assert self.context.case_id
                case, stock = db.get(AfterSalesCase, self.context.case_id), db.get(InventoryStock, "AURORA-SPORT")
                return {"case_status": case.status if case else None, "available": stock.available_quantity - stock.reserved_quantity if stock else None}
        return self._inventory()

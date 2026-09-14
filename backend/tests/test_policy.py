from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app.domain.policy import evaluate_after_sales


def order(days_ago: int, condition: str = "sealed", quality_issue: bool = False, status: str = "delivered"):
    return SimpleNamespace(
        status=status,
        delivered_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        condition=condition,
        quality_issue=quality_issue,
        amount=Decimal("299.00"),
    )


@pytest.mark.parametrize(
    ("days_ago", "condition", "eligible", "code"),
    [(7, "sealed", True, "RETURN_WITHIN_7_DAYS"), (8, "sealed", False, "REFUND_NOT_ELIGIBLE"), (3, "opened", False, "REFUND_NOT_ELIGIBLE")],
)
def test_refund_policy_boundaries(days_ago, condition, eligible, code):
    decision = evaluate_after_sales(order(days_ago, condition), "refund")
    assert decision.eligible is eligible
    assert decision.policy_code == code
    if eligible:
        assert decision.eligible_amount == Decimal("299.00")


@pytest.mark.parametrize(
    ("days_ago", "eligible", "code"),
    [(30, True, "QUALITY_EXCHANGE_WITHIN_30_DAYS"), (31, False, "EXCHANGE_NOT_ELIGIBLE")],
)
def test_quality_exchange_policy_boundaries(days_ago, eligible, code):
    decision = evaluate_after_sales(order(days_ago, "opened", True), "exchange")
    assert decision.eligible is eligible
    assert decision.policy_code == code


def test_undelivered_future_and_unknown_requests_are_rejected():
    assert evaluate_after_sales(order(1, status="shipped"), "refund").policy_code == "ORDER_NOT_DELIVERED"
    assert evaluate_after_sales(order(-1), "refund").policy_code == "ORDER_NOT_DELIVERED"
    assert evaluate_after_sales(order(1), "reship").policy_code == "UNSUPPORTED_REQUEST"

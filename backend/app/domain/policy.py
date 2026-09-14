from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..models import Order


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    policy_code: str
    explanation: str
    eligible_amount: Decimal | None = None


@dataclass(frozen=True)
class DispositionDecision:
    disposition: str
    policy_code: str
    explanation: str
    priority: str = "normal"


def evaluate_after_sales(order: Order, request_type: str, now: datetime | None = None) -> EligibilityDecision:
    now = now or datetime.now(timezone.utc)
    days_since_delivery = (now.date() - order.delivered_at.date()).days

    if order.status != "delivered":
        return EligibilityDecision(False, "ORDER_NOT_DELIVERED", "订单尚未签收，暂不能发起售后。")
    if days_since_delivery < 0:
        return EligibilityDecision(False, "ORDER_NOT_DELIVERED", "订单签收时间异常，暂不能发起售后。")

    if request_type == "refund":
        if days_since_delivery <= 7 and order.condition == "sealed":
            return EligibilityDecision(True, "RETURN_WITHIN_7_DAYS", "签收 7 天内且商品未拆封，可申请退款。", Decimal(order.amount))
        return EligibilityDecision(False, "REFUND_NOT_ELIGIBLE", "无理由退款要求签收 7 天内且商品未拆封。")

    if request_type == "exchange":
        if days_since_delivery <= 30 and order.quality_issue:
            return EligibilityDecision(True, "QUALITY_EXCHANGE_WITHIN_30_DAYS", "质量问题在签收 30 天内，可申请换货。", Decimal(order.amount))
        return EligibilityDecision(False, "EXCHANGE_NOT_ELIGIBLE", "换货要求存在质量问题且在签收 30 天内。")

    return EligibilityDecision(False, "UNSUPPORTED_REQUEST", "暂不支持该售后类型。")


def evaluate_review_disposition(
    order: Order, request_type: str, reason: str, now: datetime | None = None, rules: dict | None = None,
) -> DispositionDecision:
    """Classify a request without granting an exception.

    Only this deterministic policy may route a rejected automated request into
    the human queue. An LLM-provided phrase is evidence, never an approval.
    """
    automatic = evaluate_after_sales(order, request_type, now)
    if automatic.eligible:
        return DispositionDecision("auto_approve", automatic.policy_code, automatic.explanation)
    rules = rules or {}
    quality_terms = tuple(rules.get("quality_dispute_terms", ("质量", "故障", "损坏", "坏了", "无法使用")))
    enabled = rules.get("quality_dispute_enabled", True)
    if enabled and request_type == "refund" and any(term in reason for term in quality_terms):
        return DispositionDecision(
            "manual_review", "QUALITY_DISPUTE_REQUIRES_EVIDENCE", "该退款请求需要运营核验质量问题材料。"
        )
    return DispositionDecision("hard_reject", automatic.policy_code, automatic.explanation)

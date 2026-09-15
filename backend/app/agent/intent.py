from __future__ import annotations

import json
import re
from typing import Protocol

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from .schemas import IntentDecision


class IntentExtractor(Protocol):
    model_name: str

    def extract(self, message: str) -> IntentDecision: ...


class KeywordIntentExtractor:
    """Offline fallback used for local demos and deterministic tests."""

    model_name = "keyword-fallback-v1"

    def extract(self, message: str) -> IntentDecision:
        if any(word in message for word in ("发票", "补发", "修改地址", "改地址", "支付", "直接完成")):
            return IntentDecision(intent="unknown")
        order = re.search(r"\b(O\d+)\b", message, flags=re.IGNORECASE)
        case = re.search(r"(?:售后单|case)\s*#?\s*(\d+)", message, flags=re.IGNORECASE)
        order_id = order.group(1).upper() if order else None
        case_id = int(case.group(1)) if case else None
        if case_id is not None and any(word in message for word in ("进度", "到哪", "状态", "退款到账", "退款完成", "换货发出")):
            return IntentDecision(intent="query_fulfillment", case_id=case_id)
        request_type = "exchange" if "换" in message else "refund" if "退" in message else None
        knowledge_markers = ("政策", "规则", "流程", "时效", "多久", "怎么处理", "如何处理", "取件后")
        if order_id is None and any(marker in message for marker in knowledge_markers):
            return IntentDecision(intent="knowledge_qa")
        explicit_review = any(word in message for word in ("人工审核", "人工处理", "申诉"))
        quality_refund = request_type == "refund" and any(word in message for word in ("质量问题", "故障", "损坏", "坏了", "无法使用"))
        if request_type == "refund" and (explicit_review or quality_refund):
            return IntentDecision(intent="request_manual_review", order_id=order_id, request_type=request_type, reason=message)
        if any(word in message for word in ("物流", "快递", "到哪", "运单")):
            return IntentDecision(intent="query_logistics", order_id=order_id)
        if any(word in message for word in ("能退", "可以退", "资格", "能换", "可换")):
            return IntentDecision(intent="check_eligibility", order_id=order_id, request_type=request_type)
        if "取件" in message or "上门取" in message:
            has_time = any(marker in message for marker in ("今天", "明天", "后天", "上午", "下午", "晚上", "点", "月", "日", "-"))
            return IntentDecision(intent="schedule_pickup", case_id=case_id, time_slot=message if has_time else None)
        if request_type is not None:
            return IntentDecision(intent="create_after_sales", order_id=order_id, request_type=request_type, reason=message)
        if order_id:
            return IntentDecision(intent="query_order", order_id=order_id)
        return IntentDecision(intent="unknown")


class OpenAIIntentExtractor:
    """OpenAI-compatible JSON-mode adapter; policy decisions never come from it."""

    model_name: str

    def __init__(self, base_url: str, api_key: str, model: str):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model
        self.fallback = KeywordIntentExtractor()

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=0.2, max=1), reraise=True)
    def extract(self, message: str) -> IntentDecision:
        # Unsupported capabilities are an allowlist boundary, not a prompt-only
        # convention. Skip the model so no accidental M1 read/write can occur.
        if any(word in message for word in ("发票", "补发", "修改地址", "改地址", "支付", "直接完成")):
            return IntentDecision(intent="unknown")
        # Clear operational requests are deterministic.  Routing them before
        # the network call avoids both an avoidable model round trip and a
        # generative misclassification as policy consultation.
        fallback = self.fallback.extract(message)
        fast_intents = {"create_after_sales", "request_manual_review", "schedule_pickup"}
        if fallback.intent in fast_intents:
            return fallback
        if fallback.intent in {"query_order", "query_logistics"} and fallback.order_id:
            return fallback
        if fallback.intent == "query_fulfillment" and fallback.case_id:
            return fallback

        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是电商售后意图解析器。只返回 JSON，字段必须匹配："
                        "intent(query_order/query_logistics/query_fulfillment/check_eligibility/create_after_sales/request_manual_review/schedule_pickup/knowledge_qa/unknown)，"
                        "order_id，case_id，request_type(refund/exchange)，reason，time_slot。"
                        "增加 knowledge_qa：没有具体订单号、只咨询已发布的售后政策、流程、时效时使用它。"
                        "客户带售后单编号询问取件、退款、换货进度或状态时使用 query_fulfillment。"
                        "发票、补发、修改地址、支付、直接完成售后等未支持能力必须返回 unknown，即使文本包含订单号。"
                        "不得编造订单号、权限、资格或工具结果。"
                        "客户明确要求人工审核、申诉，或退款理由为质量问题/故障/损坏时，若有订单号和退款类型，返回 request_manual_review。"
                    ),
                },
                {"role": "user", "content": message},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM_EMPTY_RESPONSE")
        decision = IntentDecision.model_validate(json.loads(content))
        # The model may conservatively label a message containing an explicit
        # order operation as unknown. This fallback only supplies a constrained
        # intent from the same user text; it never supplies policy or identity.
        # M3 only permits quality-dispute refunds into the review workflow.
        # Quality exchanges remain on M1's deterministic automatic path.
        if decision.intent == "request_manual_review" and decision.request_type != "refund":
            return IntentDecision(
                intent="create_after_sales", order_id=decision.order_id, request_type=decision.request_type, reason=decision.reason or message
            )
        if decision.intent in {"create_after_sales", "check_eligibility"} and not decision.order_id and fallback.intent == "knowledge_qa":
            return fallback
        if decision.intent == "unknown" and fallback.intent != "unknown":
            return fallback
        return decision

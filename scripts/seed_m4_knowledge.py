"""Create original, demo-only M4 knowledge documents and queue indexing.

The text deliberately describes this repository's simulated policy; it is not
copied from, or represented as, a real marketplace policy.
"""

from __future__ import annotations

from backend.app.database import SessionLocal
from backend.app.domain.knowledge import create_document, queue_ingestion
from backend.app.domain.service import DomainError
from backend.app.schemas import KnowledgeDocumentCreateRequest


DEMO_DOCUMENTS = [
    {
        "stable_key": "after-sales-refund-policy", "title": "演示售后：退款政策", "audience": "customer", "category": "after-sales",
        "content_markdown": """# 退款政策（演示）

本演示系统中，商品签收后七天内且保持未拆封状态时，可以提交退款申请。具体订单是否符合条件，必须根据订单签收时间和商品状态进行系统校验。

创建退款申请后，客户需要再次确认；确认完成后，系统才会进入取件流程。""",
    },
    {
        "stable_key": "after-sales-exchange-policy", "title": "演示售后：换货政策", "audience": "customer", "category": "after-sales",
        "content_markdown": """# 换货政策（演示）

商品存在质量问题时，可以提交换货申请。系统会结合订单事实和已发布策略进行判断；质量争议退款可能进入人工审核，但人工审核不代表一定批准。

获批的例外方案仍需要客户结构化确认，系统不会直接执行退款或换货。""",
    },
    {
        "stable_key": "pickup-flow", "title": "演示售后：上门取件流程", "audience": "customer", "category": "logistics",
        "content_markdown": """# 上门取件流程（演示）

客户确认售后单后，可在原会话提供上午、下午或晚上的取件时段。预约成功后，等待模拟物流系统更新处理状态。

如果还没有确认售后单，请先完成售后方案确认，避免重复预约。""",
    },
    {
        "stable_key": "quality-review-sop", "title": "运营 SOP：质量争议审核", "audience": "operator", "category": "review",
        "content_markdown": """# 质量争议审核 SOP（仅运营）

审核人员应核验客户的故障描述和补充材料，使用结构化原因码记录决策。不得向客户承诺审核一定通过。

批准例外后系统会创建新的待确认售后单；原人工审核状态保持为历史事实，不能重新打开或改写。""",
    },
]


def main() -> None:
    created = queued = 0
    with SessionLocal() as db:
        for item in DEMO_DOCUMENTS:
            try:
                _, version = create_document(
                    db, "OPS_MANAGER", KnowledgeDocumentCreateRequest(**item), f"m4-demo-document-{item['stable_key']}-v1",
                )
                created += 1
            except DomainError as error:
                if error.code != "KNOWLEDGE_STABLE_KEY_EXISTS":
                    raise
                continue
            queue_ingestion(db, "OPS_MANAGER", version.id, f"m4-demo-ingestion-{item['stable_key']}-v1")
            queued += 1
    print(f"created {created} M4 demo document(s); queued {queued} ingestion job(s)")


if __name__ == "__main__":
    main()

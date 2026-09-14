"""Create idempotent, non-destructive fixture data for the M7 portfolio demo."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.app.database import SessionLocal
from backend.app.models import Actor, Logistics, Order, User
from backend.app.seed import seed_demo_data


DEMO_USER_ID = "U7001"
REFUND_ORDER_ID = "O7001"
REVIEW_ORDER_ID = "O7003"


def main() -> None:
    """Add only M7-owned rows; existing development and acceptance data remains intact."""
    created: list[str] = []
    with SessionLocal() as db:
        seed_demo_data(db)
        now = datetime.now(timezone.utc)
        if db.get(User, DEMO_USER_ID) is None:
            db.add(User(id=DEMO_USER_ID, name="M7 演示客户"))
            created.append(f"user:{DEMO_USER_ID}")
        if db.get(Actor, DEMO_USER_ID) is None:
            db.add(Actor(id=DEMO_USER_ID, display_name="M7 演示客户", role="customer"))
            created.append(f"actor:{DEMO_USER_ID}")
        db.flush()
        fixtures = (
            (REFUND_ORDER_ID, "M7-DEMO · 未拆封退款订单", Decimal("329.00"), now - timedelta(days=2), "sealed", False, "M7-SF-REFUND-001"),
            (REVIEW_ORDER_ID, "M7-DEMO · 质量争议退款订单", Decimal("479.00"), now - timedelta(days=12), "opened", True, "M7-SF-REVIEW-001"),
        )
        for order_id, item_name, amount, delivered_at, condition, quality_issue, tracking_number in fixtures:
            if db.get(Order, order_id) is None:
                db.add(Order(
                    id=order_id, user_id=DEMO_USER_ID, item_name=item_name, amount=amount,
                    status="delivered", delivered_at=delivered_at, condition=condition, quality_issue=quality_issue,
                ))
                db.flush()
                db.add(Logistics(order_id=order_id, status="delivered", tracking_number=tracking_number))
                created.append(f"order:{order_id}")
        db.commit()
    state = "created=" + ",".join(created) if created else "already_seeded=true"
    print(f"m7_scenario {state}")


if __name__ == "__main__":
    main()

"""Versioned, deterministic fault tasks. These labels never enter model prompts."""
import random

VARIANTS = {
    "read_once": "inventory_temporarily_unavailable",
    "read_twice": "inventory_temporarily_unavailable",
    "read_persistent": "inventory_temporarily_unavailable",
    "read_verification": "inventory_temporarily_unavailable",
    "write_before_commit": "refund_outcome_unknown",
    "write_remote_only": "refund_outcome_unknown",
    "write_response_lost": "refund_outcome_unknown",
    "write_query_unavailable": "refund_outcome_unknown",
    "stock_restored": "inventory_changed_during_confirmation",
    "stock_alternative": "inventory_changed_during_confirmation",
    "stock_refund": "inventory_changed_during_confirmation",
    "stock_wait_human": "inventory_changed_during_confirmation",
}


def generate_cases():
    cases = []
    for vi, (variant, family) in enumerate(VARIANTS.items()):
        for i in range(25):
            seed = 2026101201 + vi * 25 + i
            rng = random.Random(seed)
            qty = rng.randint(1, 3)
            user = ("请处理这笔售后", "这个商品有问题，请帮我售后", "我需要处理这笔订单")[i % 3]
            choice = {"stock_restored": "exchange", "stock_alternative": "exchange", "stock_refund": "refund"}.get(variant)
            if variant == "stock_wait_human": choice = ("wait", "human", "reject", "no_reply", "withdraw")[i % 5]
            expected = "completed"
            if variant == "read_persistent" or choice == "human": expected = "human"
            if choice == "wait": expected = "waiting_stock"
            if choice in {"no_reply", "reject"}: expected = "awaiting_customer"
            if choice == "withdraw": expected = "cancelled"
            fail_primary = {"read_once": [1], "read_twice": [1,2], "read_persistent": list(range(1,10)), "read_verification": [1]}.get(variant, [])
            cases.append({"id": f"{variant}-{i+1:03}", "scenario_id": family, "variant": variant, "seed": seed,
                "split": "development" if i < 20 else "holdout", "episode": i+1,
                "initial": {"order_id": f"R{seed}", "sku": f"REC-{vi}-{i%5}", "title": f"售后商品{vi}-{i%5}",
                    "quantity": qty, "unit_amount": rng.randint(50, 450), "available": qty+rng.randint(0,8)},
                "user_goal": f"{user}，商品数量 {qty} 件。",
                "fault_script": {"primary_failures": fail_primary, "verify_failures": [1] if variant in {"read_verification", "write_query_unavailable"} else []},
                "customer": {"choice": choice, "replacement": "alternative" if variant == "stock_alternative" else "original"},
                "expected": {"terminal": expected}, "schema_version": 2})
    return cases

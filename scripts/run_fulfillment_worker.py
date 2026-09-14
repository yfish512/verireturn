"""Drain one or more durable M5 Outbox events using demo adapters.

Production process managers run this as a separate worker deployment. The demo
adapter acknowledges pickup requests and in-app notifications but never creates
provider callbacks; those must arrive through the signed webhook endpoint.
"""
from backend.app.database import SessionLocal
from backend.app.domain.fulfillment import process_one_outbox


def main() -> None:
    processed = 0
    while True:
        with SessionLocal() as db:
            event_id = process_one_outbox(db)
        if event_id is None:
            break
        processed += 1
    print(f"processed_outbox_events={processed}")


if __name__ == "__main__":
    main()

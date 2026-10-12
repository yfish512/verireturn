"""Run expiry and retention maintenance once; daemon wrapper can schedule it."""
import os
from backend.app.database import SessionLocal
from backend.app.domain.exchange import release_expired_reservations
from backend.app.domain.privacy import purge_expired_messages

def main() -> None:
    retention = int(os.getenv("MESSAGE_RETENTION_DAYS", "90"))
    with SessionLocal() as db:
        inventory = release_expired_reservations(db)
    from backend.app.domain.resolutions import refresh_waiting_resolutions
    with SessionLocal() as db:
        restock = refresh_waiting_resolutions(db)
    from backend.app.agent.memory import TaskMemory
    repaired = TaskMemory(SessionLocal).reconcile_business_tasks()
    with SessionLocal() as db:
        messages = purge_expired_messages(db, retention)
    print(f"expired_inventory_reservations={inventory} redacted_messages={messages} restock_proposals={restock} repaired_tasks={repaired}")
if __name__ == "__main__": main()

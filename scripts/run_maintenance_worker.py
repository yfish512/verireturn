"""Run expiry and retention maintenance once; daemon wrapper can schedule it."""
import os
from backend.app.database import SessionLocal
from backend.app.domain.exchange import release_expired_reservations
from backend.app.domain.privacy import purge_expired_messages

def main() -> None:
    retention = int(os.getenv("MESSAGE_RETENTION_DAYS", "90"))
    with SessionLocal() as db:
        inventory = release_expired_reservations(db)
    with SessionLocal() as db:
        messages = purge_expired_messages(db, retention)
    print(f"expired_inventory_reservations={inventory} redacted_messages={messages}")
if __name__ == "__main__": main()

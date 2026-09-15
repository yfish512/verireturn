"""Project overdue human-review tickets to an auditable terminal SLA state."""
from backend.app.database import SessionLocal
from backend.app.domain.review_service import expire_overdue_review_tickets

def main() -> None:
    with SessionLocal() as db:
        print(f"expired_review_tickets={expire_overdue_review_tickets(db)}")
if __name__ == "__main__": main()

"""Publish M4 demo versions after `run_knowledge_worker.py` finishes."""

from __future__ import annotations

from sqlalchemy import select

from backend.app.database import SessionLocal
from backend.app.domain.knowledge import publish_version
from backend.app.models import KnowledgeDocument, KnowledgeDocumentVersion


def main() -> None:
    published = 0
    with SessionLocal() as db:
        versions = list(db.scalars(select(KnowledgeDocumentVersion).join(
            KnowledgeDocument, KnowledgeDocumentVersion.document_id == KnowledgeDocument.id
        ).where(KnowledgeDocument.stable_key.like("after-sales-%") | KnowledgeDocument.stable_key.in_([
            "pickup-flow", "quality-review-sop",
        ]), KnowledgeDocumentVersion.status == "ready")))
        for version in versions:
            publish_version(db, "OPS_MANAGER", version.id, f"m4-demo-publish-{version.id}")
            published += 1
    print(f"published {published} M4 demo version(s)")


if __name__ == "__main__":
    main()

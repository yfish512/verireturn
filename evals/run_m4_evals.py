"""Run M4 knowledge Agent cases and verify persisted evidence, not prose style."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.models import AfterSalesCase, AgentToolCall, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion, KnowledgeRetrievalLog


class EvaluationFailure(AssertionError):
    pass


class M4Evaluator:
    def __init__(self, base_url: str, database_url: str):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=45)
        self.sessions = sessionmaker(bind=create_engine(database_url))
        self.prefix = f"m4-eval-{uuid4().hex[:12]}"

    def close(self) -> None:
        self.client.close()

    def execute(self, row: dict) -> None:
        with self.sessions() as db:
            before_cases = db.scalar(select(func.count()).select_from(AfterSalesCase))
        response = self.client.post(
            f"/agent/threads/{self.prefix}-{row['id']}/messages", headers={"X-Demo-User-Id": "U001"},
            json={"message": row["message"], "message_id": f"{row['id']}-message"},
        )
        if response.status_code != 200:
            raise EvaluationFailure(f"Agent response {response.status_code}: {response.text}")
        payload = response.json()
        if payload.get("status") != "completed":
            raise EvaluationFailure(f"knowledge question should not await a transaction confirmation: {payload}")
        with self.sessions() as db:
            after_cases = db.scalar(select(func.count()).select_from(AfterSalesCase))
            calls = list(db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == payload["run_id"])))
        if after_cases != before_cases:
            raise EvaluationFailure("knowledge route wrote an after-sales case")
        if [(call.tool_name, call.status) for call in calls] != [("retrieve_knowledge", "succeeded")]:
            raise EvaluationFailure(f"expected only a successful knowledge trace, got {[(call.tool_name, call.status) for call in calls]}")
        retrieval_id = payload.get("retrieval_id")
        citations = payload.get("citations") or []
        if not retrieval_id or not citations or "依据：" not in payload.get("response", ""):
            raise EvaluationFailure("grounded answer lacks a retrieval ID, citation, or source label")
        with self.sessions() as db:
            log = db.get(KnowledgeRetrievalLog, retrieval_id)
            if log is None or log.actor_id != "U001" or log.route != "agent_knowledge_qa":
                raise EvaluationFailure("retrieval log is missing or belongs to another actor")
            if log.cited_chunk_ids != citations or not set(citations).issubset(set(log.candidate_chunk_ids)):
                raise EvaluationFailure("persisted citations are outside the retrieved evidence set")
            visible = db.execute(select(KnowledgeDocument.audience).join(
                KnowledgeDocumentVersion, KnowledgeDocument.id == KnowledgeDocumentVersion.document_id
            ).join(KnowledgeChunk, KnowledgeChunk.document_version_id == KnowledgeDocumentVersion.id).where(
                KnowledgeChunk.id.in_(citations)
            )).scalars().all()
        if not visible or any(audience not in {"customer", "shared"} for audience in visible):
            raise EvaluationFailure("customer citation crossed the knowledge audience boundary")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("EVAL_BASE_URL", "http://127.0.0.1:8004"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--cases", default="evals/cases/m4_knowledge_agent_cases.jsonl")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    evaluator = M4Evaluator(args.base_url, args.database_url)
    results = []
    try:
        for row in (json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines() if line.strip()):
            started = datetime.now(timezone.utc)
            try:
                evaluator.execute(row)
                results.append({"id": row["id"], "status": "passed", "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000)})
            except Exception as error:
                results.append({"id": row["id"], "status": "failed", "error": str(error), "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000)})
    finally:
        evaluator.close()
    report = {"total": len(results), "passed": sum(item["status"] == "passed" for item in results), "failed": sum(item["status"] == "failed" for item in results), "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

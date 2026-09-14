from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .agent.runtime import close_agent_runtime, get_agent_runtime
from .api.agent_routes import router as agent_router
from .api.knowledge_routes import knowledge_router, ops_knowledge_router
from .api.fulfillment_routes import fulfillment_router
from .api.observability_routes import agent_feedback_router, ops_observability_router
from .api.routes import router
from .api.review_routes import customer_router as review_router, ops_router
from .database import DATABASE_URL, Base, SessionLocal, engine
from .seed import seed_demo_data
from .domain.observability import overview


@asynccontextmanager
async def lifespan(_: FastAPI):
    # SQLite is used only by isolated unit tests and rapid prototypes. PostgreSQL
    # schema changes must be applied with Alembic before the API starts.
    if DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_demo_data(db)
    get_agent_runtime()
    yield
    close_agent_runtime()


app = FastAPI(
    title="VeriReturn Business Tools API",
    description="模拟电商售后业务系统。Agent 必须通过这些受约束工具执行操作。",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(agent_router)
app.include_router(review_router)
app.include_router(ops_router)
app.include_router(knowledge_router)
app.include_router(ops_knowledge_router)
app.include_router(fulfillment_router)
app.include_router(agent_feedback_router)
app.include_router(ops_observability_router)


@app.get("/health", tags=["system"])
def health_check():
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    # Values are refreshed by the protected overview path and metric worker;
    # this endpoint only exposes aggregate gauges for a scraper.
    with SessionLocal() as db:
        overview(db)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

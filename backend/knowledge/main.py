from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from knowledge.api.routes_auth import router as auth_router
from knowledge.api.routes_console import router as console_router
from knowledge.api.routes_documents import router as documents_router
from knowledge.api.routes_kbs import router as kbs_router
from knowledge.api.routes_memory import router as memory_router
from knowledge.api.routes_ops import router as ops_router
from knowledge.api.routes_search import router as search_router
from knowledge.api.routes_tasks import router as tasks_router
from knowledge.api.routes_warehouse import router as warehouse_router
from knowledge.core.settings import get_settings
from knowledge.db.base import Base
from knowledge.db.session import engine
from knowledge.services.vector_store import close_vector_store


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield
    close_vector_store()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(console_router)
app.include_router(auth_router)
app.include_router(kbs_router)
app.include_router(warehouse_router)
app.include_router(tasks_router)
app.include_router(documents_router)
app.include_router(memory_router)
app.include_router(search_router)
app.include_router(ops_router)

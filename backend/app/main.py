"""FastAPI application factory, lifespan (DB init + optional in-process worker).

Run for development:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import router as api_router
from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.providers.base import ProviderUnavailable
from app.worker import WORKER_ID, requeue_stale_jobs, worker_loop

logger = logging.getLogger("echoneura")


def _recover_on_boot() -> None:
    """Jobs left leased by a previous crashed process must not stay stuck."""
    db = SessionLocal()
    try:
        n = requeue_stale_jobs(db)
        if n:
            logger.warning("Requeued %d job(s) left behind by a previous worker", n)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    init_db()
    _recover_on_boot()

    missing = settings.active_provider_requirements()
    if missing:
        logger.error(
            "Selected providers are missing configuration: %s. "
            "Jobs will fail until this is set (or switch to the mock provider).",
            ", ".join(missing),
        )

    worker_task: asyncio.Task | None = None
    stop_event = asyncio.Event()
    if settings.worker_in_process:
        worker_task = asyncio.create_task(
            worker_loop(worker_id=WORKER_ID, stop_event=stop_event), name="echoneura-worker"
        )
        logger.info("In-process worker started (%s)", WORKER_ID)

    app.state.worker_task = worker_task
    app.state.worker_stop = stop_event
    try:
        yield
    finally:
        if worker_task is not None:
            stop_event.set()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await worker_task
            logger.info("In-process worker stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="EchoNeura API",
        description=(
            "AI Audio Content Studio — M1: upload audio, transcribe with speaker "
            "diarization, edit inline, rename speakers, export SRT/VTT/TXT/MD."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=r"https://.*\.e2b\.app$",  # sandbox live-preview host
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api")

    @app.exception_handler(ProviderUnavailable)
    async def _provider_unavailable(_request, exc: ProviderUnavailable):
        # A misconfigured provider is an operator error, not a client error.
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    return app


app = create_app()


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    """Cheap liveness probe for Railway/Render/Fly health checks."""
    from app.api.health import build_health

    return build_health()

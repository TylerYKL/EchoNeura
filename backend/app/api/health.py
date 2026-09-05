"""Health / readiness endpoint.

Doubles as an operator dashboard: it reports which providers are active, whether
their credentials are present, whether ffmpeg resolved, and what the queue looks
like. When something is misconfigured you should be able to see it in one GET.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import settings
from app.core.db import get_db
from app.models import Job, JobStatus
from app.providers.base import ProviderUnavailable
from app.providers.registry import (
    get_asr_provider,
    get_assistant_provider,
    get_diarization_provider,
    get_enrichment_provider,
    provider_summary,
)
from app.services.audio import ffmpeg_exe, ffmpeg_version
from app.worker import WORKER_ID

router = APIRouter()


def build_health(db: Session | None = None) -> dict[str, Any]:
    missing = settings.active_provider_requirements()

    ffmpeg: str | None = None
    try:
        ffmpeg = ffmpeg_version() or ffmpeg_exe()
    except Exception:  # noqa: BLE001
        ffmpeg = None

    provider_state: dict[str, str] = {}
    try:
        provider_state = provider_summary()
    except ProviderUnavailable as exc:
        provider_state = {"error": str(exc)}

    # Verify each selected provider can actually run (keys/deps present).
    ready: dict[str, bool] = {}
    for label, factory in (
        ("asr", get_asr_provider),
        ("enrichment", get_enrichment_provider),
        ("assistant", get_assistant_provider),
    ):
        try:
            factory().check_available()
            ready[label] = True
        except ProviderUnavailable:
            ready[label] = False
    diar = get_diarization_provider()
    if diar is None:
        ready["diarization"] = True
    else:
        try:
            diar.check_available()
            ready["diarization"] = True
        except ProviderUnavailable:
            ready["diarization"] = False

    worker: dict[str, Any] = {
        "mode": "in-process" if settings.worker_in_process else "standalone",
        "worker_id": WORKER_ID,
        "max_concurrent_jobs": settings.worker_max_concurrent_jobs,
    }

    database = settings.database_url.split("://", 1)[0]
    queue: dict[str, int] = {}
    if db is not None:
        rows = db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
        queue = dict(rows)
        worker["queue"] = queue
        worker["active_jobs"] = sum(
            count
            for status, count in queue.items()
            if status
            not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}
        )

    status = "ok" if (not missing and all(ready.values()) and ffmpeg) else "degraded"
    return {
        "status": status,
        "app": settings.app_name,
        "environment": settings.environment,
        "version": __version__,
        "providers": provider_state,
        "providers_ready": ready,
        "missing_config": missing,
        "ffmpeg": ffmpeg,
        "database": database,
        "worker": worker,
    }


@router.get("/health", response_model=None)
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    return build_health(db)

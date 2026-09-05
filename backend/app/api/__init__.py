"""API routers. Mounted under /api by app.main."""

from fastapi import APIRouter

from app.api import health, jobs, voice

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
router.include_router(voice.router, prefix="/voice", tags=["voice"])

__all__ = ["router"]

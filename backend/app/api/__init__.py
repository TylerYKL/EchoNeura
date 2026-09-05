"""API routers. Mounted under /api by app.main."""

from fastapi import APIRouter

from app.api import health, jobs

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])

__all__ = ["router"]

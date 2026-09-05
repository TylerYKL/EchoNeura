"""Database-backed job queue + worker.

Why not Celery/RQ?
    They need a broker. M1 targets a single container with SQLite or Postgres,
    and a `SELECT ... FOR UPDATE SKIP LOCKED`-style claim gives us the exact same
    semantics (at-most-one worker per job, crash recovery, retries) with zero
    extra infra. Redis is added in M6 *only if* we need >2 concurrent workers or
    cross-host fan-out — see docs/adr/0002-job-queue.md.

Two ways to run it:
    in-process  (default)  worker task starts with the API via FastAPI lifespan
    standalone             python -m app.worker   (own container in production)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.models import Job, JobEvent, JobStatus, utcnow
from app.pipeline.jobs import mark_failed, process_job
from app.providers.base import ProviderError

logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


class QueueFullError(RuntimeError):
    pass


def create_job(
    db: Session,
    *,
    original_filename: str,
    stored_path: str,
    mime_type: str | None,
    file_size_bytes: int,
    language: str = "auto",
    word_timestamps: bool = True,
) -> Job:
    """Insert a queued job. The worker picks it up on its next poll."""
    job = Job(
        original_filename=original_filename,
        stored_path=stored_path,
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        language=language or "auto",
        word_timestamps_requested=word_timestamps,
        status=JobStatus.QUEUED.value,
        progress=5,
        stage_message="Queued",
    )
    db.add(job)
    db.flush()
    db.add(
        JobEvent(
            job_id=job.id,
            stage=JobStatus.QUEUED.value,
            level="info",
            message=f"Job created for {original_filename} ({file_size_bytes / 1e6:.2f} MB)",
            progress=5,
        )
    )
    db.commit()
    return job


def claim_next_job(db: Session, worker_id: str = WORKER_ID) -> Job | None:
    """Atomically claim the oldest claimable job.

    Claimable = status 'queued' AND (no lease OR lease expired).

    The claim is a single conditional UPDATE guarded by `id == :id AND
    status == 'queued'`, so two workers racing for the same row cannot both win:
    the loser's UPDATE matches zero rows. That is the portable equivalent of
    `FOR UPDATE SKIP LOCKED` and works on SQLite as well as Postgres.
    """
    now = utcnow()
    candidate_id = db.scalar(
        select(Job.id)
        .where(
            Job.status == JobStatus.QUEUED.value,
            (Job.lease_expires_at.is_(None)) | (Job.lease_expires_at <= now),
        )
        .order_by(Job.created_at.asc())
        .limit(1)
    )
    if candidate_id is None:
        return None

    lease = now + timedelta(seconds=settings.worker_lease_timeout_seconds)
    # coalesce keeps the *first* start time so retries do not lose queue-wait
    # telemetry (that number is how you spot a saturated queue).
    result = db.execute(
        update(Job)
        .where(Job.id == candidate_id, Job.status == JobStatus.QUEUED.value)
        .values(
            worker_id=worker_id,
            lease_expires_at=lease,
            started_at=func.coalesce(Job.started_at, now),
        )
    )
    db.commit()
    if result.rowcount == 0:
        # Another worker won the race.
        return None

    job = db.get(Job, candidate_id)
    if job is None:  # pragma: no cover - deleted between select and update
        return None
    # The session may already hold this row from an earlier query; Core UPDATEs
    # bypass the identity map, so re-read it to see our own write.
    db.refresh(job)
    return job


def renew_lease(db: Session, job: Job, worker_id: str = WORKER_ID) -> bool:
    """Extend the lease. Returns False if we lost the job (e.g. it was requeued)."""
    lease = utcnow() + timedelta(seconds=settings.worker_lease_timeout_seconds)
    result = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.worker_id == worker_id)
        .values(lease_expires_at=lease)
    )
    db.commit()
    renewed = result.rowcount > 0
    if renewed:
        db.refresh(job)
    return renewed


def requeue_stale_jobs(db: Session) -> int:
    """Recover jobs whose worker died (lease expired while still active)."""
    now = utcnow()
    stale_ids = db.scalars(
        select(Job.id).where(
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < now,
            Job.status.notin_(
                [JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value]
            ),
        )
    ).all()
    if not stale_ids:
        return 0

    for job_id in stale_ids:
        job = db.get(Job, job_id)
        if job is None:
            continue
        if job.attempts + 1 >= job.max_attempts:
            job.status = JobStatus.FAILED.value
            job.error = job.error or "Worker lease expired and retry budget is exhausted."
            job.failed_at = now
            job.stage_message = "Failed"
            db.add(
                JobEvent(
                    job_id=job.id,
                    stage=JobStatus.FAILED.value,
                    level="error",
                    message="Worker disappeared (lease expired); no attempts left.",
                )
            )
        else:
            job.attempts += 1
            job.status = JobStatus.QUEUED.value
            job.stage_message = f"Requeued after worker loss ({job.attempts}/{job.max_attempts})"
            db.add(
                JobEvent(
                    job_id=job.id,
                    stage=JobStatus.QUEUED.value,
                    level="warn",
                    message=job.stage_message,
                )
            )
        job.lease_expires_at = None
        job.worker_id = None
    db.commit()
    return len(stale_ids)


def queue_stats(db: Session) -> dict[str, int]:
    rows = db.execute(select(Job.status, Job.id)).all()
    counts: dict[str, int] = {s.value: 0 for s in JobStatus}
    for status, _id in rows:
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = len(rows)
    return counts


def run_one(db: Session | None = None, worker_id: str = WORKER_ID) -> str | None:
    """Claim and process a single job. Returns the job id, or None if idle."""
    own_session = db is None
    db = db or SessionLocal()
    try:
        job = claim_next_job(db, worker_id)
        if job is None:
            return None
        job_id = job.id
        logger.info("Worker %s claimed job %s (%s)", worker_id, job_id, job.original_filename)
        try:
            process_job(db, job_id)
        except ProviderError as exc:
            mark_failed(db, job, exc, retryable=exc.retryable)
        except Exception as exc:  # noqa: BLE001 - the worker must never die
            mark_failed(db, job, exc, retryable=True)
        return job_id
    finally:
        if own_session:
            db.close()


async def worker_loop(
    *,
    worker_id: str = WORKER_ID,
    poll_seconds: float | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Poll the queue forever. Blocking work is pushed to a thread so the API's
    event loop stays responsive when running in-process."""
    poll = poll_seconds if poll_seconds is not None else settings.worker_poll_seconds
    stop_event = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    processed = 0
    logger.info("Worker %s started (poll=%.2fs)", worker_id, poll)

    while not stop_event.is_set():
        started = time.monotonic()
        try:
            # Opportunistic crash recovery on every idle tick is cheap and means
            # a killed container does not leave jobs stranded.
            job_id = await loop.run_in_executor(None, run_one, None, worker_id)
            if job_id is None:
                await loop.run_in_executor(None, _recover_stale)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=poll)
            else:
                processed += 1
        except asyncio.CancelledError:
            logger.info("Worker %s cancelled after %d jobs", worker_id, processed)
            raise
        except Exception:
            logger.exception("Worker loop error; continuing")
            await asyncio.sleep(max(poll, 1.0))
        # Guard against a hot loop when jobs finish faster than the poll interval.
        elapsed = time.monotonic() - started
        if elapsed < poll * 0.1:
            await asyncio.sleep(poll * 0.1)


def _recover_stale() -> None:
    db = SessionLocal()
    try:
        n = requeue_stale_jobs(db)
        if n:
            logger.warning("Recovered %d stale job(s)", n)
    finally:
        db.close()


def main() -> None:  # pragma: no cover - entrypoint
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    )
    init_db()
    logger.info("Starting standalone EchoNeura worker %s", WORKER_ID)
    logger.info(
        "Providers: asr=%s diarization=%s", settings.asr_provider, settings.diarization_provider
    )
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")


if __name__ == "__main__":  # pragma: no cover
    main()

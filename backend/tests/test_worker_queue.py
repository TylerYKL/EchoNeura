"""Queue semantics: claiming, retries, crash recovery.

These matter more than they look — a queue that double-processes a job burns API
credits, and one that strands a job after a crash looks like a hung product.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.models import Job, JobEvent, JobStatus, Segment, Speaker, Transcript, utcnow
from app.pipeline.jobs import process_job
from app.providers.base import ProviderError, ProviderUnavailable
from app.worker import (
    WORKER_ID,
    claim_next_job,
    create_job,
    queue_stats,
    renew_lease,
    requeue_stale_jobs,
    run_one,
)


def make_job(db, path: Path, *, language: str = "auto", name: str | None = None) -> Job:
    return create_job(
        db,
        original_filename=name or path.name,
        stored_path=str(path),
        mime_type="audio/wav",
        file_size_bytes=path.stat().st_size,
        language=language,
    )


def test_create_job_starts_queued(db, sample_wav: Path) -> None:
    job = make_job(db, sample_wav)
    assert job.status == JobStatus.QUEUED.value
    assert job.progress == 5
    assert job.attempts == 0
    assert db.scalar(select(JobEvent).where(JobEvent.job_id == job.id)) is not None


def test_claim_returns_oldest_queued_job_first(db, sample_wav: Path) -> None:
    first = make_job(db, sample_wav, name="first.wav")
    second = make_job(db, sample_wav, name="second.wav")
    # Force a deterministic ordering regardless of clock resolution.
    first.created_at = utcnow() - timedelta(seconds=10)
    db.commit()

    claimed = claim_next_job(db)
    assert claimed is not None and claimed.id == first.id
    assert claimed.worker_id == WORKER_ID
    assert claimed.lease_expires_at is not None
    assert claimed.started_at is not None

    claimed2 = claim_next_job(db)
    assert claimed2 is not None and claimed2.id == second.id


def test_claimed_job_is_not_claimed_again(db, sample_wav: Path) -> None:
    job = make_job(db, sample_wav)
    assert claim_next_job(db).id == job.id
    # Still leased and not queued -> nothing to claim.
    assert claim_next_job(db) is None


def test_claim_returns_none_on_empty_queue(db) -> None:
    assert claim_next_job(db) is None


def test_expired_lease_makes_a_job_claimable_again(db, sample_wav: Path) -> None:
    job = make_job(db, sample_wav)
    claimed = claim_next_job(db)
    claimed.lease_expires_at = utcnow() - timedelta(seconds=1)
    claimed.status = JobStatus.QUEUED.value
    db.commit()

    reclaimed = claim_next_job(db)
    assert reclaimed is not None and reclaimed.id == job.id


def test_renew_lease_extends_and_detects_loss(db, sample_wav: Path) -> None:
    job = make_job(db, sample_wav)
    claimed = claim_next_job(db)
    before = claimed.lease_expires_at
    assert renew_lease(db, claimed) is True
    assert claimed.lease_expires_at >= before

    # Simulate another worker stealing the row.
    claimed.worker_id = "someone-else"
    db.commit()
    assert renew_lease(db, job) is False


def test_stale_active_job_is_requeued(db, sample_wav: Path) -> None:
    job = make_job(db, sample_wav)
    claimed = claim_next_job(db)
    claimed.status = JobStatus.TRANSCRIBING.value
    claimed.lease_expires_at = utcnow() - timedelta(seconds=5)
    db.commit()

    assert requeue_stale_jobs(db) == 1
    db.refresh(job)
    assert job.status == JobStatus.QUEUED.value
    assert job.attempts == 1
    assert job.lease_expires_at is None
    assert job.worker_id is None


def test_stale_job_without_attempts_left_fails(db, sample_wav: Path) -> None:
    job = make_job(db, sample_wav)
    job.max_attempts = 1
    db.commit()
    claimed = claim_next_job(db)
    claimed.status = JobStatus.TRANSCRIBING.value
    claimed.lease_expires_at = utcnow() - timedelta(seconds=5)
    db.commit()

    assert requeue_stale_jobs(db) == 1
    db.refresh(job)
    assert job.status == JobStatus.FAILED.value
    assert job.failed_at is not None


def test_completed_jobs_are_never_requeued(db, sample_wav: Path) -> None:
    make_job(db, sample_wav)
    job_id = run_one(db)
    assert job_id is not None
    # Even with an ancient lease, a terminal job is left alone.
    job = db.get(Job, job_id)
    job.lease_expires_at = utcnow() - timedelta(days=1)
    db.commit()
    assert requeue_stale_jobs(db) == 0
    assert job.status == JobStatus.COMPLETED.value


def test_run_one_processes_and_persists(db, long_sample_wav: Path) -> None:
    job = make_job(db, long_sample_wav)
    assert run_one(db) == job.id

    db.refresh(job)
    assert job.status == JobStatus.COMPLETED.value
    assert job.progress == 100
    assert job.completed_at is not None
    assert job.error is None
    assert job.audio_duration_seconds == 90.0
    assert job.asr_provider == "mock"
    assert job.detected_language == "en"

    transcript = db.scalar(select(Transcript).where(Transcript.job_id == job.id))
    assert transcript is not None
    assert transcript.full_text
    assert transcript.revision == 1

    segments = db.scalars(select(Segment).where(Segment.transcript_id == transcript.id)).all()
    assert len(segments) > 3
    assert all(s.text for s in segments)
    assert all(s.end >= s.start for s in segments)
    assert any(s.words_json for s in segments)

    speakers = db.scalars(select(Speaker).where(Speaker.transcript_id == transcript.id)).all()
    assert len(speakers) == 2
    assert {s.display_name for s in speakers} == {"Speaker 1", "Speaker 2"}
    assert sum(s.word_count for s in speakers) == sum(len(s.text.split()) for s in segments)


def test_run_one_is_idempotent_on_empty_queue(db) -> None:
    assert run_one(db) is None


def test_failed_job_retries_then_fails(db, sample_wav: Path, monkeypatch) -> None:
    """Retryable errors go back to queued; non-retryable ones fail immediately."""
    job = make_job(db, sample_wav)
    job.max_attempts = 3
    db.commit()

    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise ProviderError("vendor 503")

    monkeypatch.setattr("app.pipeline.jobs.stage_transcribe", boom)

    assert run_one(db) == job.id
    db.refresh(job)
    assert job.status == JobStatus.QUEUED.value, "retryable errors must be requeued"
    assert job.attempts == 1
    assert "vendor 503" in job.error
    # The backoff lease stops an instant hot-loop retry.
    assert job.lease_expires_at is not None

    job.lease_expires_at = None
    db.commit()
    assert run_one(db) == job.id
    db.refresh(job)
    assert job.attempts == 2

    job.lease_expires_at = None
    db.commit()
    assert run_one(db) == job.id
    db.refresh(job)
    assert job.status == JobStatus.FAILED.value
    assert job.attempts == 3
    assert calls["n"] == 3


def test_missing_credentials_fail_without_retry(db, sample_wav: Path, monkeypatch) -> None:
    job = make_job(db, sample_wav)
    job.max_attempts = 5
    db.commit()

    def unavailable(*args, **kwargs):
        raise ProviderUnavailable("ECHONEURA_ASSEMBLYAI_API_KEY is not set")

    monkeypatch.setattr("app.pipeline.jobs.stage_transcribe", unavailable)
    assert run_one(db) == job.id
    db.refresh(job)
    assert job.status == JobStatus.FAILED.value, "config errors must not burn retries"
    assert job.attempts == 1
    assert "API_KEY" in job.error


def test_missing_source_file_fails_cleanly(db, tmp_path: Path) -> None:
    ghost = tmp_path / "deleted.wav"
    job = create_job(
        db,
        original_filename="deleted.wav",
        stored_path=str(ghost),
        mime_type="audio/wav",
        file_size_bytes=1,
    )
    assert run_one(db) == job.id
    db.refresh(job)
    assert job.status == JobStatus.FAILED.value
    assert "missing" in job.error.lower()


def test_process_job_twice_replaces_the_transcript(db, sample_wav: Path) -> None:
    """Reprocessing must not duplicate segments (a classic re-run bug)."""
    job = make_job(db, sample_wav)
    process_job(db, job.id)
    first_count = len(
        db.scalars(
            select(Segment)
            .join(Transcript, Segment.transcript_id == Transcript.id)
            .where(Transcript.job_id == job.id)
        ).all()
    )

    process_job(db, job.id)
    transcripts = db.scalars(select(Transcript).where(Transcript.job_id == job.id)).all()
    assert len(transcripts) == 1
    segments = db.scalars(select(Segment).where(Segment.transcript_id == transcripts[0].id)).all()
    assert len(segments) == first_count


def test_separate_diarization_provider_is_used(db, sample_wav: Path, monkeypatch) -> None:
    """With diarization_provider=mock, the mock diarizer labels a non-diarizing ASR."""
    from app.providers.mock import MockASRProvider, MockDiarizationProvider
    from app.providers.registry import get_asr_provider, get_diarization_provider

    monkeypatch.setattr(settings, "diarization_provider", "mock")
    get_asr_provider.cache_clear()
    get_diarization_provider.cache_clear()

    class NoDiarMock(MockASRProvider):
        supports_diarization = False

    monkeypatch.setattr("app.providers.registry.MockASRProvider", NoDiarMock)
    monkeypatch.setattr("app.providers.registry.MockDiarizationProvider", MockDiarizationProvider)

    job = make_job(db, sample_wav)
    process_job(db, job.id)
    db.refresh(job)
    assert job.status == JobStatus.COMPLETED.value
    assert job.diarization_provider == "mock"

    transcript = db.scalar(select(Transcript).where(Transcript.job_id == job.id))
    speakers = db.scalars(select(Speaker).where(Speaker.transcript_id == transcript.id)).all()
    assert len(speakers) >= 1


def test_queue_stats_counts_every_state(db, sample_wav: Path) -> None:
    make_job(db, sample_wav, name="a.wav")
    make_job(db, sample_wav, name="b.wav")
    run_one(db)
    stats = queue_stats(db)
    assert stats["total"] == 2
    assert stats[JobStatus.COMPLETED.value] == 1
    assert stats[JobStatus.QUEUED.value] == 1

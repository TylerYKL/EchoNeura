"""SQLAlchemy models.

Entities
--------
Job        one upload + its processing lifecycle (the state machine lives here)
Transcript the enriched output of a job (full text, summary, quotes)  [M1/M2]
Speaker    a diarized voice inside a transcript; display_name is user-editable
Segment    one timestamped line, owned by a speaker; text is user-editable
JobEvent   append-only audit/progress log used to drive the UI activity feed

Design notes
------------
* `Job.status` is the single source of truth for the pipeline state machine.
* Worker claiming is done with an atomic conditional UPDATE (see
  `app.worker.claim_next_job`) so multiple workers can share one queue without
  Redis. On SQLite that is safe under WAL; on Postgres it becomes
  `SELECT ... FOR UPDATE SKIP LOCKED`.
* `lease_expires_at` makes a crashed worker's job recoverable instead of stuck.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class JobStatus(StrEnum):
    """Pipeline state machine.

    queued -> preprocessing -> transcribing -> diarizing -> merging
           -> aligning -> completed
    any state -> failed | cancelled
    """

    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    TRANSCRIBING = "transcribing"
    DIARIZING = "diarizing"
    MERGING = "merging"
    ALIGNING = "aligning"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}

    @property
    def is_active(self) -> bool:
        return not self.is_terminal


# Ordered list used to compute a default progress percentage.
ACTIVE_STAGE_ORDER: tuple[JobStatus, ...] = (
    JobStatus.QUEUED,
    JobStatus.PREPROCESSING,
    JobStatus.TRANSCRIBING,
    JobStatus.DIARIZING,
    JobStatus.MERGING,
    JobStatus.ALIGNING,
    JobStatus.COMPLETED,
)

STAGE_PROGRESS: dict[JobStatus, int] = {
    JobStatus.QUEUED: 5,
    JobStatus.PREPROCESSING: 15,
    JobStatus.TRANSCRIBING: 45,
    JobStatus.DIARIZING: 70,
    JobStatus.MERGING: 85,
    JobStatus.ALIGNING: 95,
    JobStatus.COMPLETED: 100,
    JobStatus.FAILED: 100,
    JobStatus.CANCELLED: 100,
}


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(
        String(24), default=JobStatus.QUEUED.value, index=True, nullable=False
    )

    # --- Source file ---
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str | None] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audio_duration_seconds: Mapped[float | None] = mapped_column(Float)

    # --- Request options ---
    language: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    detected_language: Mapped[str | None] = mapped_column(String(16))
    word_timestamps_requested: Mapped[bool] = mapped_column(default=True, nullable=False)

    # --- Providers actually used (recorded so results are reproducible) ---
    asr_provider: Mapped[str | None] = mapped_column(String(32))
    diarization_provider: Mapped[str | None] = mapped_column(String(32))
    enrich_provider: Mapped[str | None] = mapped_column(String(32))

    # --- Progress / queue bookkeeping ---
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_message: Mapped[str | None] = mapped_column(String(512))
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    transcript: Mapped[Transcript | None] = relationship(
        back_populates="job",
        uselist=False,
        cascade="all, delete-orphan",
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobEvent.created_at",
    )

    @property
    def status_enum(self) -> JobStatus:
        return JobStatus(self.status)


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    full_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    # M2 fields — present now so the schema does not need a breaking migration.
    summary: Mapped[str | None] = mapped_column(Text)
    key_points_json: Mapped[str | None] = mapped_column(Text)
    quotes_json: Mapped[str | None] = mapped_column(Text)
    # Bumped on every user edit so SRT/exports can be cached safely.
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    job: Mapped[Job] = relationship(back_populates="transcript")
    speakers: Mapped[list[Speaker]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="Speaker.speaker_index",
    )
    segments: Mapped[list[Segment]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="Segment.start",
    )


class Speaker(Base):
    __tablename__ = "speakers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    transcript_id: Mapped[str] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Raw provider label, never mutated: SPEAKER_00, A, speaker-1 ...
    speaker_key: Mapped[str] = mapped_column(String(64), nullable=False)
    speaker_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # User-editable (plan M1 requirement: "Rename speaker labels").
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16))
    is_edited: Mapped[bool] = mapped_column(default=False, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    transcript: Mapped[Transcript] = relationship(back_populates="speakers")
    segments: Mapped[list[Segment]] = relationship(back_populates="speaker")


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (Index("ix_segments_transcript_start", "transcript_id", "start"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    transcript_id: Mapped[str] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    speaker_id: Mapped[str | None] = mapped_column(
        ForeignKey("speakers.id", ondelete="SET NULL"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    start: Mapped[float] = mapped_column(Float, nullable=False)
    end: Mapped[float] = mapped_column(Float, nullable=False)
    # original_text is immutable provenance; text is what the user sees/edits.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    is_edited: Mapped[bool] = mapped_column(default=False, nullable=False)
    # M1 deliverable: word-level timestamps (JSON list of {w,s,e}).
    words_json: Mapped[str | None] = mapped_column(Text)

    transcript: Mapped[Transcript] = relationship(back_populates="segments")
    speaker: Mapped[Speaker | None] = relationship(back_populates="segments")

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_created", "job_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )

    job: Mapped[Job] = relationship(back_populates="events")

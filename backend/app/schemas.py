"""Pydantic request/response schemas (API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Speaker / Segment
# --------------------------------------------------------------------------- #
class SpeakerOut(ORMModel):
    id: str
    speaker_key: str
    speaker_index: int
    display_name: str
    color: str | None = None
    is_edited: bool = False
    word_count: int = 0
    segment_count: int = 0


class SpeakerRenameIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("display_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("display_name must not be blank")
        return v


class WordOut(BaseModel):
    w: str
    s: float
    e: float


class SegmentOut(ORMModel):
    id: str
    seq: int
    start: float
    end: float
    text: str
    original_text: str
    confidence: float | None = None
    is_edited: bool = False
    speaker_id: str | None = None
    speaker_display_name: str | None = None
    speaker_color: str | None = None
    words: list[WordOut] = Field(default_factory=list)


class SegmentUpdateIn(BaseModel):
    """Inline correction of a single segment (M1: 'Inline transcript editing')."""

    text: str | None = Field(default=None, min_length=0, max_length=20_000)
    speaker_id: str | None = None
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)


class SegmentPatchIn(BaseModel):
    """One entry of a bulk patch: segment id + the fields to change."""

    id: str
    text: str | None = Field(default=None, min_length=0, max_length=20_000)
    speaker_id: str | None = None
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)


class BulkSegmentsUpdateIn(BaseModel):
    updates: list[SegmentPatchIn] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Job
# --------------------------------------------------------------------------- #
class JobCreateResponse(ORMModel):
    id: str
    status: str
    created_at: datetime


class JobEventOut(ORMModel):
    stage: str
    level: str
    message: str
    progress: int | None = None
    created_at: datetime


class JobOut(ORMModel):
    id: str
    status: str
    progress: int
    stage_message: str | None = None
    original_filename: str
    file_size_bytes: int
    audio_duration_seconds: float | None = None
    mime_type: str | None = None
    language: str
    detected_language: str | None = None
    asr_provider: str | None = None
    diarization_provider: str | None = None
    attempts: int
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None


class JobDetailOut(JobOut):
    transcript: TranscriptOut | None = None
    events: list[JobEventOut] = Field(default_factory=list)


class TranscriptOut(ORMModel):
    id: str
    full_text: str | None = None
    language: str | None = None
    revision: int
    summary: str | None = None
    key_points: list[str] = Field(default_factory=list)
    quotes: list[dict[str, Any]] = Field(default_factory=list)
    speakers: list[SpeakerOut] = Field(default_factory=list)
    segments: list[SegmentOut] = Field(default_factory=list)


class JobListOut(BaseModel):
    total: int
    items: list[JobOut]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    app: str
    environment: str
    version: str
    providers: dict[str, str]
    missing_config: list[str] = Field(default_factory=list)
    ffmpeg: str | None = None
    database: str | None = None
    worker: dict[str, Any] = Field(default_factory=dict)


class ExportFormatIn(BaseModel):
    include_speaker_names: bool = True
    max_chars_per_line: int = Field(default=42, ge=10, le=120)
    max_lines_per_cue: int = Field(default=2, ge=1, le=4)


JobDetailOut.model_rebuild()

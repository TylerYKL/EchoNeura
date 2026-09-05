"""The M1 processing pipeline: the job state machine, stage by stage.

    queued -> preprocessing -> transcribing -> diarizing -> merging
           -> aligning -> completed

Each stage is a small function so it can be unit-tested in isolation, and the
whole thing is provider-agnostic: it only ever touches the interfaces in
`app.providers.base`.

Persistence is committed after every stage change, so a crash mid-run leaves an
accurate trail in `job_events` instead of a silently stuck job.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    STAGE_PROGRESS,
    Job,
    JobEvent,
    JobStatus,
    Segment,
    Speaker,
    Transcript,
    utcnow,
)
from app.pipeline.merge import MergeStats, assign_speakers
from app.providers.base import (
    ASRResult,
    DiarizationResult,
    ProviderUnavailable,
)
from app.providers.registry import get_asr_provider, get_diarization_provider
from app.services.audio import AudioInfo, convert_to_model_input, probe

logger = logging.getLogger(__name__)

# A stable palette so speaker colours survive reloads and look intentional.
SPEAKER_COLORS = (
    "#6366f1",  # indigo
    "#0ea5e9",  # sky
    "#10b981",  # emerald
    "#f59e0b",  # amber
    "#ef4444",  # red
    "#a855f7",  # purple
    "#14b8a6",  # teal
    "#f97316",  # orange
)


class ProgressReporter(Protocol):
    def __call__(self, status: JobStatus, message: str, *, progress: int | None = None) -> None: ...


@dataclass(slots=True)
class StageResult:
    audio_info: AudioInfo | None = None
    normalized_path: Path | None = None
    asr: ASRResult | None = None
    diarization: DiarizationResult | None = None
    merge_stats: MergeStats | None = None
    segment_count: int = 0
    speaker_count: int = 0


class JobNotFoundError(Exception):
    pass


def get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    return job


def log_event(
    db: Session,
    job: Job,
    stage: str,
    message: str,
    *,
    level: str = "info",
    progress: int | None = None,
    commit: bool = True,
) -> JobEvent:
    event = JobEvent(job_id=job.id, stage=stage, level=level, message=message, progress=progress)
    db.add(event)
    if commit:
        db.commit()
    return event


def set_status(
    db: Session,
    job: Job,
    status: JobStatus,
    message: str | None = None,
    *,
    progress: int | None = None,
) -> None:
    job.status = status.value
    if message is not None:
        job.stage_message = message
    job.progress = progress if progress is not None else STAGE_PROGRESS.get(status, job.progress)
    db.add(
        JobEvent(
            job_id=job.id,
            stage=status.value,
            level="info",
            message=message or f"stage={status.value}",
            progress=job.progress,
        )
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #
def stage_preprocess(db: Session, job: Job, src: Path) -> tuple[AudioInfo, Path | None]:
    """Probe the upload; normalise to 16 kHz mono WAV when a local model needs it."""
    set_status(db, job, JobStatus.PREPROCESSING, "Probing audio")
    info = probe(src)
    job.audio_duration_seconds = info.duration_seconds
    db.commit()
    log_event(
        db,
        job,
        JobStatus.PREPROCESSING.value,
        f"duration={info.duration_seconds:.2f}s codec={info.codec} "
        f"rate={info.sample_rate}Hz channels={info.channels}",
        commit=False,
    )

    asr = get_asr_provider()
    needs_wav = asr.name.startswith("faster_whisper") or settings.diarization_provider == "pyannote"
    normalized: Path | None = None
    if needs_wav:
        set_status(db, job, JobStatus.PREPROCESSING, "Normalising to 16 kHz mono WAV", progress=25)
        normalized = src.with_name(f"{src.stem}.16k.wav")
        convert_to_model_input(src, normalized)
        log_event(
            db,
            job,
            JobStatus.PREPROCESSING.value,
            f"normalised -> {normalized.name}",
            commit=False,
        )
    db.commit()
    return info, normalized


def stage_transcribe(db: Session, job: Job, audio_path: Path, duration: float | None) -> ASRResult:
    """Run ASR. If the vendor bundles diarization, ask for it in the same call."""
    asr = get_asr_provider()
    asr.check_available()
    # Ask the vendor to diarize only if it can AND we have no better separate
    # diarizer configured. A configured pyannote/mock diarizer wins, because it
    # runs on the original audio rather than the vendor's downmixed stream.
    separate_diar = get_diarization_provider()
    has_real_separate_diar = separate_diar is not None and separate_diar.name not in {"none"}
    want_diar = asr.supports_diarization and not has_real_separate_diar
    set_status(
        db,
        job,
        JobStatus.TRANSCRIBING,
        f"Transcribing with {asr.name}" + (" (+diarization)" if asr.supports_diarization else ""),
    )
    job.asr_provider = asr.name
    db.commit()

    result = asr.transcribe(
        audio_path,
        language=job.language or "auto",
        duration_seconds=duration,
        want_diarization=want_diar,
        want_words=bool(job.word_timestamps_requested),
    )
    job.detected_language = result.language
    log_event(
        db,
        job,
        JobStatus.TRANSCRIBING.value,
        f"{len(result.segments)} segments, language={result.language}, provider={result.provider}",
        commit=False,
    )
    db.commit()
    return result


def stage_diarize(
    db: Session, job: Job, audio_path: Path, asr: ASRResult, duration: float | None
) -> DiarizationResult | None:
    """Run a separate diarization pass only when the ASR vendor didn't already."""
    if asr.has_speaker_labels:
        log_event(
            db,
            job,
            JobStatus.DIARIZING.value,
            "ASR provider returned speaker labels; skipping standalone diarization",
        )
        job.diarization_provider = "bundled"
        db.commit()
        return None

    diar = get_diarization_provider()
    if diar is None:
        job.diarization_provider = "bundled"
        db.commit()
        return None

    set_status(db, job, JobStatus.DIARIZING, f"Diarizing with {diar.name}")
    diar.check_available()
    job.diarization_provider = diar.name
    db.commit()
    result = diar.diarize(audio_path, duration_seconds=duration)
    log_event(
        db,
        job,
        JobStatus.DIARIZING.value,
        f"{len(result.turns)} turns, {result.speaker_count or len(result.speaker_keys)} speakers",
        commit=False,
    )
    db.commit()
    return result


def stage_merge(
    db: Session, job: Job, asr: ASRResult, diar: DiarizationResult | None
) -> tuple[ASRResult, MergeStats]:
    set_status(db, job, JobStatus.MERGING, "Assigning speakers to segments")
    merged, stats = assign_speakers(asr, diar)
    log_event(
        db,
        job,
        JobStatus.MERGING.value,
        f"segments {stats.segments_in}->{stats.segments_out} "
        f"(assigned={stats.assigned}, split={stats.split}, unknown={stats.unassigned})",
        commit=False,
    )
    db.commit()
    return merged, stats


def stage_persist(db: Session, job: Job, asr: ASRResult) -> tuple[Transcript, int, int]:
    """Write Transcript + Speakers + Segments. Re-running replaces prior output."""
    set_status(db, job, JobStatus.ALIGNING, "Saving transcript")

    existing = db.scalar(select(Transcript).where(Transcript.job_id == job.id))
    if existing is not None:
        db.delete(existing)
        db.flush()

    transcript = Transcript(
        job_id=job.id,
        full_text=asr.text,
        language=asr.language or job.detected_language or job.language,
    )
    db.add(transcript)
    db.flush()

    # Preserve any human renames if the job is re-processed: key by speaker_key.
    speaker_keys: list[str] = []
    for seg in asr.segments:
        key = seg.speaker or "S0"
        if key not in speaker_keys:
            speaker_keys.append(key)

    speakers: dict[str, Speaker] = {}
    for i, key in enumerate(speaker_keys):
        speakers[key] = Speaker(
            transcript_id=transcript.id,
            speaker_key=key,
            speaker_index=i,
            display_name="Speaker" if key == "UNKNOWN" else f"Speaker {i + 1}",
            color=SPEAKER_COLORS[i % len(SPEAKER_COLORS)],
        )
        db.add(speakers[key])
    db.flush()

    for seq, seg in enumerate(asr.segments):
        key = seg.speaker or "S0"
        speaker = speakers.get(key) or speakers.get("S0")
        words_json = (
            json.dumps(
                [{"w": w.text, "s": round(w.start, 3), "e": round(w.end, 3)} for w in seg.words],
                ensure_ascii=False,
            )
            if seg.words
            else None
        )
        db.add(
            Segment(
                transcript_id=transcript.id,
                speaker_id=speaker.id if speaker else None,
                seq=seq,
                start=round(float(seg.start), 3),
                end=round(float(seg.end), 3),
                text=seg.text.strip(),
                original_text=seg.text.strip(),
                confidence=seg.confidence,
                words_json=words_json,
            )
        )
    db.flush()

    # Recompute per-speaker word counts now that segments exist.
    for speaker in speakers.values():
        rows = db.scalars(select(Segment).where(Segment.speaker_id == speaker.id)).all()
        speaker.word_count = sum(len(r.text.split()) for r in rows)

    db.commit()
    return transcript, len(asr.segments), len(speakers)


def mark_completed(db: Session, job: Job, segment_count: int, speaker_count: int) -> None:
    job.status = JobStatus.COMPLETED.value
    job.progress = 100
    job.stage_message = f"Done — {segment_count} segments, {speaker_count} speakers"
    job.completed_at = utcnow()
    job.error = None
    db.add(
        JobEvent(
            job_id=job.id,
            stage=JobStatus.COMPLETED.value,
            level="info",
            message=job.stage_message,
            progress=100,
        )
    )
    db.commit()


def mark_failed(db: Session, job: Job, exc: BaseException, *, retryable: bool = True) -> None:
    message = str(exc).strip() or exc.__class__.__name__
    job.attempts += 1
    can_retry = retryable and job.attempts < job.max_attempts
    if can_retry:
        job.status = JobStatus.QUEUED.value
        job.progress = STAGE_PROGRESS[JobStatus.QUEUED]
        job.stage_message = f"Retrying after error ({job.attempts}/{job.max_attempts})"
        job.error = message[:4000]
        # Backoff: give a flaky vendor time to recover before re-claiming.
        job.lease_expires_at = utcnow() + timedelta(seconds=10 * (2 ** (job.attempts - 1)))
        level = "warn"
    else:
        job.status = JobStatus.FAILED.value
        job.progress = STAGE_PROGRESS[JobStatus.FAILED]
        job.stage_message = "Failed"
        job.error = message[:4000]
        job.failed_at = utcnow()
        job.lease_expires_at = None
        level = "error"
    db.add(
        JobEvent(
            job_id=job.id,
            stage=job.status,
            level=level,
            message=message[:2000],
            progress=job.progress,
        )
    )
    db.commit()
    logger.exception("Job %s failed: %s", job.id, message, exc_info=exc)


def process_job(db: Session, job_id: str) -> StageResult:
    """Run the full M1 pipeline for one job. Raises on unrecoverable failure."""
    job = get_job(db, job_id)
    out = StageResult()

    if not job.stored_path:
        # Retrying cannot bring the file back, so fail fast instead of burning
        # the retry budget (and the operator's patience).
        raise ProviderUnavailable(f"Job {job.id} has no stored audio path")
    src = Path(job.stored_path)
    if not src.exists():
        raise ProviderUnavailable(f"Uploaded file is missing from disk: {src}")

    job.started_at = job.started_at or utcnow()
    db.commit()

    # 1. preprocess
    info, normalized = stage_preprocess(db, job, src)
    out.audio_info, out.normalized_path = info, normalized

    # Which file do the models get? Local models want normalised WAV; cloud
    # vendors accept (and often prefer) the original container.
    model_input = normalized or src

    # 2. transcribe
    asr = stage_transcribe(db, job, model_input, info.duration_seconds)
    out.asr = asr

    # 3. diarize (only if the ASR vendor did not)
    diar = stage_diarize(db, job, model_input, asr, info.duration_seconds)
    out.diarization = diar

    # 4. merge speakers into segments
    asr, stats = stage_merge(db, job, asr, diar)
    out.asr, out.merge_stats = asr, stats

    # 5. persist
    _transcript, segments, speakers = stage_persist(db, job, asr)
    out.segment_count, out.speaker_count = segments, speakers

    mark_completed(db, job, segments, speakers)

    # The normalised WAV is a build artifact, not a user asset — drop it so a
    # long recording does not double the storage bill.
    if normalized is not None:
        try:
            normalized.unlink(missing_ok=True)
            out.normalized_path = None
        except OSError:
            logger.warning("Could not remove temp file %s", normalized)

    return out

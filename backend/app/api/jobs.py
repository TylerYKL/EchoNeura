"""Job endpoints: upload, inspect, edit, export.

M1 surface
    POST   /api/jobs                       upload audio -> queued job
    GET    /api/jobs                       list (status filter, paging)
    GET    /api/jobs/{id}                  job + transcript + events
    DELETE /api/jobs/{id}                  delete job, transcript and file
    POST   /api/jobs/{id}/cancel           stop a queued/running job
    POST   /api/jobs/{id}/reprocess        re-run the pipeline (keeps nothing)
    GET    /api/jobs/{id}/audio            stream the original upload (player)
    GET    /api/jobs/{id}/export/{fmt}     srt | vtt | txt | md | json
    PATCH  /api/jobs/{id}/speakers/{sid}   rename a speaker
    PATCH  /api/jobs/{id}/segments/{segid} inline text/timing/speaker edit
    PATCH  /api/jobs/{id}/segments         bulk edit (used by autosave)
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.serializers import job_detail_out, job_out, segment_out, speaker_out
from app.core.config import settings
from app.core.db import get_db
from app.models import Job, JobEvent, JobStatus, Segment, Speaker, Transcript, utcnow
from app.pipeline.jobs import log_event
from app.pipeline.srt import (
    ExportOptions,
    SpeakerRef,
    build_cues_from_models,
    cues_to_srt,
    cues_to_vtt,
    segments_to_markdown,
    segments_to_transcript_txt,
)
from app.schemas import (
    BulkSegmentsUpdateIn,
    JobDetailOut,
    JobListOut,
    JobOut,
    SegmentOut,
    SegmentUpdateIn,
    SpeakerOut,
    SpeakerRenameIn,
)
from app.services.audio import AUDIO_EXTENSIONS, looks_like_audio
from app.worker import create_job

logger = logging.getLogger(__name__)
router = APIRouter()

ExportFormat = Literal["srt", "vtt", "txt", "md", "json"]
SUPPORTED_LANGUAGES = {
    "auto",
    "en",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "nl",
    "sv",
    "no",
    "da",
    "fi",
    "pl",
    "cs",
    "ro",
    "hu",
    "el",
    "tr",
    "ru",
    "uk",
    "ar",
    "he",
    "hi",
    "bn",
    "ta",
    "te",
    "mr",
    "gu",
    "kn",
    "ml",
    "pa",
    "ur",
    "id",
    "ms",
    "th",
    "vi",
    "tl",
    "zh",
    "ja",
    "ko",
}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
CHUNK = 1024 * 1024


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = _UNSAFE.sub("_", stem).strip("._") or "audio"
    return stem[:80]


def _load_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    return job


def _load_transcript(db: Session, job: Job) -> Transcript:
    if job.transcript is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Job {job.id} has no transcript yet (status={job.status}).",
        )
    return job.transcript


def _ordered_segments(db: Session, transcript_id: str) -> list[Segment]:
    return list(
        db.scalars(
            select(Segment)
            .where(Segment.transcript_id == transcript_id)
            .order_by(Segment.start, Segment.seq)
        )
    )


def _speaker_map(db: Session, transcript_id: str) -> dict[str, Speaker]:
    speakers = db.scalars(select(Speaker).where(Speaker.transcript_id == transcript_id)).all()
    return {s.id: s for s in speakers}


def _refresh_full_text(db: Session, transcript: Transcript) -> None:
    """Rebuild full_text from segments after an edit and bump the revision."""
    segments = _ordered_segments(db, transcript.id)
    transcript.full_text = "\n".join(s.text for s in segments if s.text.strip())
    transcript.revision += 1
    transcript.updated_at = utcnow()


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def upload_job(
    file: Annotated[UploadFile, File(description="Audio or video file to transcribe")],
    db: Annotated[Session, Depends(get_db)],
    language: Annotated[str, Form()] = "auto",
    word_timestamps: Annotated[bool, Form()] = True,
) -> JobOut:
    """Accept an upload, persist it, and enqueue a processing job."""
    filename = file.filename or "upload.bin"
    if not looks_like_audio(filename, file.content_type):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{filename}'. Send audio or video "
            f"({', '.join(sorted(AUDIO_EXTENSIONS))}).",
        )
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            422,
            f"Unsupported language '{language}'. Use 'auto' or one of: "
            f"{', '.join(sorted(SUPPORTED_LANGUAGES))}",
        )

    # Create the job row first so the upload directory is namespaced by job id
    # and a half-written file can always be cleaned up.
    job = create_job(
        db,
        original_filename=filename,
        stored_path=None,  # set below, once we know the job id
        mime_type=file.content_type,
        file_size_bytes=0,
        language=language,
        word_timestamps=word_timestamps,
    )

    job_dir = settings.upload_dir / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    dest = job_dir / f"{_safe_stem(filename)}{Path(filename).suffix.lower() or '.bin'}"

    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(CHUNK):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"File exceeds the {settings.max_upload_mb} MB limit.",
                    )
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        db.delete(job)
        db.commit()
        raise
    except OSError as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        db.delete(job)
        db.commit()
        logger.exception("Failed writing upload for job %s", job.id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Could not store upload: {exc}"
        ) from exc
    finally:
        await file.close()

    if written == 0:
        shutil.rmtree(job_dir, ignore_errors=True)
        db.delete(job)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty.")

    job.stored_path = str(dest)
    job.file_size_bytes = written
    db.commit()
    logger.info("Job %s queued: %s (%.2f MB)", job.id, filename, written / 1e6)
    return job_out(job)


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
@router.get("", response_model=JobListOut)
def list_jobs(
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListOut:
    query = select(Job)
    if status_filter is not None:
        query = query.where(Job.status == status_filter.value)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    jobs = db.scalars(query.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
    return JobListOut(total=int(total), items=[job_out(j) for j in jobs])


@router.get("/{job_id}", response_model=JobDetailOut)
def get_job_detail(job_id: str, db: Annotated[Session, Depends(get_db)]) -> JobDetailOut:
    return job_detail_out(db, _load_job(db, job_id))


@router.get("/{job_id}/audio")
def get_job_audio(job_id: str, db: Annotated[Session, Depends(get_db)]) -> FileResponse:
    """Serve the original upload for the built-in player (supports HTTP Range)."""
    job = _load_job(db, job_id)
    if not job.stored_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This job has no stored audio.")
    path = Path(job.stored_path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "The stored audio file is no longer on disk.")
    return FileResponse(
        path,
        media_type=job.mime_type or "application/octet-stream",
        filename=path.name,
    )


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
@router.get("/{job_id}/export/{fmt}")
def export_transcript(
    job_id: str,
    fmt: ExportFormat,
    db: Annotated[Session, Depends(get_db)],
    include_speaker_names: Annotated[bool, Query()] = True,
    include_timestamps: Annotated[bool, Query()] = True,
    max_chars_per_line: Annotated[int, Query(ge=10, le=120)] = 42,
    max_lines_per_cue: Annotated[int, Query(ge=1, le=4)] = 2,
) -> Response:
    """Export the *current* (possibly human-edited) transcript."""
    job = _load_job(db, job_id)
    transcript = _load_transcript(db, job)
    segments = _ordered_segments(db, transcript.id)
    speakers = _speaker_map(db, transcript.id)
    refs = {
        sid: SpeakerRef(display_name=s.display_name, color=s.color) for sid, s in speakers.items()
    }
    stem = _safe_stem(job.original_filename)

    if not segments:
        raise HTTPException(status.HTTP_409_CONFLICT, "Transcript has no segments to export.")

    options = ExportOptions(
        include_speaker_names=include_speaker_names,
        max_chars_per_line=max_chars_per_line,
        max_lines_per_cue=max_lines_per_cue,
    )

    if fmt in {"srt", "vtt"}:
        cues = build_cues_from_models(segments, refs, options)
        body = (
            cues_to_srt(
                cues,
                include_speaker_names=include_speaker_names,
                max_chars_per_line=max_chars_per_line,
                max_lines_per_cue=max_lines_per_cue,
            )
            if fmt == "srt"
            else cues_to_vtt(
                cues,
                include_speaker_names=include_speaker_names,
                max_chars_per_line=max_chars_per_line,
                max_lines_per_cue=max_lines_per_cue,
            )
        )
        media = "application/x-subrip" if fmt == "srt" else "text/vtt"
    elif fmt == "txt":
        body = segments_to_transcript_txt(
            segments,
            refs,
            include_timestamps=include_timestamps,
            include_speaker_names=include_speaker_names,
        )
        media = "text/plain"
    elif fmt == "md":
        body = segments_to_markdown(
            segments, refs, title=stem, include_timestamps=include_timestamps
        )
        media = "text/markdown"
    else:  # json — the full-fidelity export used for re-import and archiving
        from app.api.serializers import transcript_out

        payload = {
            "job": job_out(job).model_dump(mode="json"),
            "transcript": transcript_out(db, transcript).model_dump(mode="json"),
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        media = "application/json"

    return Response(
        content=body.encode("utf-8"),
        media_type=f"{media}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}.{fmt}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{job_id}/transcript.txt", response_class=PlainTextResponse)
def transcript_as_text(job_id: str, db: Annotated[Session, Depends(get_db)]) -> str:
    """Browser-friendly inline view (not an attachment) for quick copy/paste."""
    job = _load_job(db, job_id)
    transcript = _load_transcript(db, job)
    segments = _ordered_segments(db, transcript.id)
    speakers = _speaker_map(db, transcript.id)
    refs = {
        sid: SpeakerRef(display_name=s.display_name, color=s.color) for sid, s in speakers.items()
    }
    return segments_to_transcript_txt(segments, refs)


# --------------------------------------------------------------------------- #
# Edit — speakers
# --------------------------------------------------------------------------- #
@router.patch("/{job_id}/speakers/{speaker_id}", response_model=SpeakerOut)
def rename_speaker(
    job_id: str,
    speaker_id: str,
    payload: SpeakerRenameIn,
    db: Annotated[Session, Depends(get_db)],
) -> SpeakerOut:
    """Rename a speaker label (M1: 'Rename speaker labels')."""
    job = _load_job(db, job_id)
    transcript = _load_transcript(db, job)
    speaker = db.get(Speaker, speaker_id)
    if speaker is None or speaker.transcript_id != transcript.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Speaker {speaker_id} not found on this job"
        )

    speaker.display_name = payload.display_name
    speaker.is_edited = True
    _refresh_full_text(db, transcript)
    db.commit()
    count = db.scalar(select(func.count(Segment.id)).where(Segment.speaker_id == speaker.id)) or 0
    log_event(db, job, "edit", f"Renamed speaker {speaker.speaker_key} -> '{payload.display_name}'")
    return speaker_out(speaker, int(count))


@router.patch("/{job_id}/speakers/{speaker_id}/color", response_model=SpeakerOut)
def recolor_speaker(
    job_id: str,
    speaker_id: str,
    db: Annotated[Session, Depends(get_db)],
    color: Annotated[str, Query(pattern=r"^#[0-9a-fA-F]{6}$")],
) -> SpeakerOut:
    job = _load_job(db, job_id)
    transcript = _load_transcript(db, job)
    speaker = db.get(Speaker, speaker_id)
    if speaker is None or speaker.transcript_id != transcript.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Speaker {speaker_id} not found on this job"
        )
    speaker.color = color.lower()
    db.commit()
    count = db.scalar(select(func.count(Segment.id)).where(Segment.speaker_id == speaker.id)) or 0
    return speaker_out(speaker, int(count))


# --------------------------------------------------------------------------- #
# Edit — segments
# --------------------------------------------------------------------------- #
def _apply_segment_patch(
    db: Session,
    transcript: Transcript,
    speakers: dict[str, Speaker],
    segment: Segment,
    patch: SegmentUpdateIn,
) -> Segment:
    changed = False
    if patch.text is not None:
        new_text = patch.text.strip()
        if new_text != segment.text:
            segment.text = new_text
            segment.is_edited = True
            changed = True
    if patch.speaker_id is not None and patch.speaker_id != segment.speaker_id:
        if patch.speaker_id not in speakers:
            raise HTTPException(
                422,
                f"speaker_id {patch.speaker_id} does not belong to this transcript",
            )
        old_id = segment.speaker_id
        segment.speaker_id = patch.speaker_id
        changed = True
        db.flush()
        for sid in {old_id, patch.speaker_id}:
            if sid and sid in speakers:
                speakers[sid].word_count = _word_count(db, sid)
    if patch.start is not None and patch.start >= 0 and patch.start != segment.start:
        segment.start = round(patch.start, 3)
        changed = True
    if patch.end is not None and patch.end >= 0 and patch.end != segment.end:
        segment.end = round(patch.end, 3)
        changed = True
    if changed and segment.end < segment.start:
        raise HTTPException(
            422,
            f"Segment {segment.id}: end ({segment.end}) is before start ({segment.start})",
        )
    return segment


def _word_count(db: Session, speaker_id: str) -> int:
    rows = db.scalars(select(Segment.text).where(Segment.speaker_id == speaker_id)).all()
    return sum(len(r.split()) for r in rows)


@router.patch("/{job_id}/segments/{segment_id}", response_model=SegmentOut)
def update_segment(
    job_id: str,
    segment_id: str,
    payload: SegmentUpdateIn,
    db: Annotated[Session, Depends(get_db)],
) -> SegmentOut:
    """Inline transcript correction (M1: 'Inline transcript editing')."""
    job = _load_job(db, job_id)
    transcript = _load_transcript(db, job)
    segment = db.get(Segment, segment_id)
    if segment is None or segment.transcript_id != transcript.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Segment {segment_id} not found on this job"
        )

    speakers = _speaker_map(db, transcript.id)
    _apply_segment_patch(db, transcript, speakers, segment, payload)
    _refresh_full_text(db, transcript)
    db.commit()
    log_event(
        db, job, "edit", f"Edited segment {segment.seq} ({segment.start:.2f}-{segment.end:.2f}s)"
    )
    return segment_out(segment, speakers.get(segment.speaker_id or ""))


@router.patch("/{job_id}/segments", response_model=list[SegmentOut])
def update_segments_bulk(
    job_id: str,
    payload: BulkSegmentsUpdateIn,
    db: Annotated[Session, Depends(get_db)],
) -> list[SegmentOut]:
    """Bulk edit used by the UI's debounced autosave."""
    if not payload.updates:
        return []
    job = _load_job(db, job_id)
    transcript = _load_transcript(db, job)
    speakers = _speaker_map(db, transcript.id)

    wanted = {u.id for u in payload.updates}
    segments = {
        s.id: s
        for s in db.scalars(
            select(Segment).where(Segment.transcript_id == transcript.id, Segment.id.in_(wanted))
        )
    }
    missing = wanted - segments.keys()
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Segment(s) not found on this job: {', '.join(sorted(missing))}",
        )

    out: list[SegmentOut] = []
    for update_item in payload.updates:
        segment = segments[update_item.id]
        patch = SegmentUpdateIn(
            text=update_item.text,
            speaker_id=update_item.speaker_id,
            start=update_item.start,
            end=update_item.end,
        )
        _apply_segment_patch(db, transcript, speakers, segment, patch)
        out.append(segment)

    _refresh_full_text(db, transcript)
    db.commit()
    log_event(db, job, "edit", f"Bulk-edited {len(out)} segment(s)")
    return [segment_out(s, speakers.get(s.speaker_id or "")) for s in out]


@router.post("/{job_id}/segments/reset", response_model=JobDetailOut)
def reset_edits(job_id: str, db: Annotated[Session, Depends(get_db)]) -> JobDetailOut:
    """Revert all inline edits back to the model output (keeps speaker names)."""
    job = _load_job(db, job_id)
    transcript = _load_transcript(db, job)
    segments = _ordered_segments(db, transcript.id)
    for segment in segments:
        segment.text = segment.original_text
        segment.is_edited = False
    speakers = _speaker_map(db, transcript.id)
    for speaker in speakers.values():
        speaker.word_count = _word_count(db, speaker.id)
    _refresh_full_text(db, transcript)
    db.commit()
    log_event(db, job, "edit", f"Reset {len(segments)} segment(s) to model output")
    return job_detail_out(db, job)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, db: Annotated[Session, Depends(get_db)]) -> JobOut:
    job = _load_job(db, job_id)
    if JobStatus(job.status).is_terminal:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Job is already {job.status} and cannot be cancelled."
        )
    job.status = JobStatus.CANCELLED.value
    job.stage_message = "Cancelled by user"
    job.lease_expires_at = None
    db.add(
        JobEvent(
            job_id=job.id,
            stage=JobStatus.CANCELLED.value,
            level="warn",
            message="Cancelled by user",
            progress=job.progress,
        )
    )
    db.commit()
    return job_out(job)


@router.post("/{job_id}/reprocess", response_model=JobOut)
def reprocess_job(job_id: str, db: Annotated[Session, Depends(get_db)]) -> JobOut:
    """Re-run the pipeline (e.g. after switching providers). Discards the transcript."""
    job = _load_job(db, job_id)
    if not job.stored_path or not Path(job.stored_path).exists():
        raise HTTPException(
            status.HTTP_410_GONE, "The original audio file is gone; upload it again."
        )
    if job.transcript is not None:
        db.delete(job.transcript)
        db.flush()
    job.status = JobStatus.QUEUED.value
    job.progress = 5
    job.stage_message = "Queued for reprocessing"
    job.error = None
    job.attempts = 0
    job.failed_at = None
    job.completed_at = None
    job.lease_expires_at = None
    job.worker_id = None
    db.add(
        JobEvent(
            job_id=job.id,
            stage=JobStatus.QUEUED.value,
            level="info",
            message="Reprocessing requested; previous transcript discarded.",
            progress=5,
        )
    )
    db.commit()
    return job_out(job)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: str, db: Annotated[Session, Depends(get_db)]) -> Response:
    job = _load_job(db, job_id)
    if job.stored_path:
        shutil.rmtree(Path(job.stored_path).parent, ignore_errors=True)
    db.delete(job)
    db.commit()
    return Response(status_code=204)

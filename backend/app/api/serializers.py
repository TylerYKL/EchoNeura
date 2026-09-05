"""ORM -> API schema mapping.

Kept separate from the routers so the same shape is used by the list endpoint,
the detail endpoint and the tests.
"""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy.orm import Session

from app.models import Job, JobEvent, Segment, Speaker, Transcript
from app.schemas import (
    JobDetailOut,
    JobEventOut,
    JobOut,
    SegmentOut,
    SpeakerOut,
    TranscriptOut,
    WordOut,
)


def parse_words(segment: Segment) -> list[WordOut]:
    if not segment.words_json:
        return []
    try:
        raw = json.loads(segment.words_json)
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[WordOut] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                WordOut(
                    w=str(item.get("w", "")), s=float(item.get("s", 0)), e=float(item.get("e", 0))
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def speaker_out(speaker: Speaker, segment_count: int = 0) -> SpeakerOut:
    return SpeakerOut(
        id=speaker.id,
        speaker_key=speaker.speaker_key,
        speaker_index=speaker.speaker_index,
        display_name=speaker.display_name,
        color=speaker.color,
        is_edited=speaker.is_edited,
        word_count=speaker.word_count,
        segment_count=segment_count,
    )


def segment_out(segment: Segment, speaker: Speaker | None = None) -> SegmentOut:
    return SegmentOut(
        id=segment.id,
        seq=segment.seq,
        start=segment.start,
        end=segment.end,
        text=segment.text,
        original_text=segment.original_text,
        confidence=segment.confidence,
        is_edited=segment.is_edited,
        speaker_id=segment.speaker_id,
        speaker_display_name=speaker.display_name if speaker else None,
        speaker_color=speaker.color if speaker else None,
        words=parse_words(segment),
    )


def transcript_out(db: Session, transcript: Transcript) -> TranscriptOut:
    segments = (
        db.query(Segment)
        .filter(Segment.transcript_id == transcript.id)
        .order_by(Segment.start, Segment.seq)
        .all()
    )
    speakers = (
        db.query(Speaker)
        .filter(Speaker.transcript_id == transcript.id)
        .order_by(Speaker.speaker_index)
        .all()
    )
    counts = Counter(s.speaker_id for s in segments if s.speaker_id)
    by_id = {sp.id: sp for sp in speakers}

    try:
        key_points = json.loads(transcript.key_points_json) if transcript.key_points_json else []
    except json.JSONDecodeError:
        key_points = []
    try:
        quotes = json.loads(transcript.quotes_json) if transcript.quotes_json else []
    except json.JSONDecodeError:
        quotes = []

    return TranscriptOut(
        id=transcript.id,
        full_text=transcript.full_text,
        language=transcript.language,
        revision=transcript.revision,
        summary=transcript.summary,
        key_points=key_points if isinstance(key_points, list) else [],
        quotes=quotes if isinstance(quotes, list) else [],
        speakers=[speaker_out(sp, counts.get(sp.id, 0)) for sp in speakers],
        segments=[segment_out(s, by_id.get(s.speaker_id)) for s in segments],
    )


def job_out(job: Job) -> JobOut:
    return JobOut.model_validate(job)


def job_detail_out(db: Session, job: Job) -> JobDetailOut:
    events = (
        db.query(JobEvent)
        .filter(JobEvent.job_id == job.id)
        .order_by(JobEvent.created_at, JobEvent.id)
        .all()
    )
    base = job_out(job).model_dump()
    return JobDetailOut(
        **base,
        transcript=transcript_out(db, job.transcript) if job.transcript else None,
        events=[JobEventOut.model_validate(e) for e in events],
    )

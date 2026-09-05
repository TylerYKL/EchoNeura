"""Assign speakers to ASR segments using a diarization timeline.

This is the step that makes or breaks a transcript. Getting it wrong means two
people collapsed into one paragraph, which then poisons every summary and quote
generated downstream.

Algorithm: weighted time-overlap voting.
  For each ASR segment, compute how many seconds it overlaps each diarization
  turn, weighted by the turn's confidence, and pick the best-scoring speaker.
  Segments whose speech is split across a speaker change are *split* at the
  boundary (when word timings are available) rather than assigned wholesale,
  because a merged line is the exact failure mode we are trying to avoid.

Complexity is O(segments * turns) which is fine for hour-long recordings
(a few thousand turns); swap to an interval tree if that ever changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.providers.base import ASRResult, ASRSegment, ASRWord, DiarizationResult

# A segment must overlap its winning speaker by at least this fraction of its own
# duration, otherwise we label it unknown rather than guessing.
MIN_OVERLAP_FRACTION = 0.25


@dataclass(slots=True)
class MergeStats:
    segments_in: int = 0
    segments_out: int = 0
    assigned: int = 0
    unassigned: int = 0
    split: int = 0
    speakers: int = 0


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def best_speaker(
    seg_start: float, seg_end: float, diar: DiarizationResult
) -> tuple[str | None, float]:
    """Return (speaker_key, overlap_fraction) for a time range."""
    duration = max(1e-6, seg_end - seg_start)
    scores: dict[str, float] = {}
    for turn in diar.turns:
        ov = _overlap(seg_start, seg_end, turn.start, turn.end)
        if ov <= 0:
            continue
        weight = turn.confidence if turn.confidence is not None else 1.0
        scores[turn.speaker] = scores.get(turn.speaker, 0.0) + ov * weight
    if not scores:
        return None, 0.0
    speaker, score = max(scores.items(), key=lambda kv: kv[1])
    return speaker, min(1.0, score / duration)


def _split_segment_at(
    segment: ASRSegment, boundary: float, left_speaker: str, right_speaker: str
) -> list[ASRSegment]:
    """Split a segment into two at `boundary` using word timings when possible."""
    if not segment.words:
        # No word timings: duplicate the text is wrong, so hand the whole line to
        # the dominant speaker and record nothing. Caller handles labelling.
        return [segment]

    left_words = [w for w in segment.words if (w.start + w.end) / 2 <= boundary]
    right_words = [w for w in segment.words if (w.start + w.end) / 2 > boundary]
    if not left_words or not right_words:
        return [segment]

    def build(words: list[ASRWord], speaker: str) -> ASRSegment:
        return ASRSegment(
            text=" ".join(w.text for w in words).strip(),
            start=words[0].start,
            end=words[-1].end,
            speaker=speaker,
            confidence=segment.confidence,
            words=words,
        )

    return [build(left_words, left_speaker), build(right_words, right_speaker)]


def _find_internal_speaker_change(
    segment: ASRSegment, diar: DiarizationResult
) -> tuple[float, str, str] | None:
    """Detect a speaker change *inside* a segment and return (boundary, left, right)."""
    if not segment.words or segment.duration < 1.0:
        return None

    # Score each half of the segment independently; if they disagree, split.
    mid_candidates = [
        (segment.words[i].end + segment.words[i + 1].start) / 2
        for i in range(len(segment.words) - 1)
    ]
    best: tuple[float, str, str] | None = None
    best_gain = 0.0
    for boundary in mid_candidates:
        left_sp, left_frac = best_speaker(segment.start, boundary, diar)
        right_sp, right_frac = best_speaker(boundary, segment.end, diar)
        if not left_sp or not right_sp or left_sp == right_sp:
            continue
        gain = (left_frac + right_frac) / 2
        if gain > best_gain and min(left_frac, right_frac) >= MIN_OVERLAP_FRACTION:
            best_gain = gain
            best = (boundary, left_sp, right_sp)
    return best


def assign_speakers(asr: ASRResult, diar: DiarizationResult | None) -> tuple[ASRResult, MergeStats]:
    """Return a new ASRResult whose segments carry speaker labels."""
    stats = MergeStats(segments_in=len(asr.segments))

    if diar is None or not diar.turns:
        for seg in asr.segments:
            if seg.speaker is None:
                seg.speaker = "S0"
                stats.unassigned += 1
            else:
                stats.assigned += 1
        stats.segments_out = len(asr.segments)
        stats.speakers = len({s.speaker for s in asr.segments if s.speaker})
        return asr, stats

    out: list[ASRSegment] = []
    for seg in asr.segments:
        if seg.speaker:
            # Vendor already diarized (AssemblyAI/Deepgram) — trust it.
            out.append(seg)
            stats.assigned += 1
            continue

        speaker, frac = best_speaker(seg.start, seg.end, diar)

        if speaker and frac >= MIN_OVERLAP_FRACTION:
            change = _find_internal_speaker_change(seg, diar)
            if change and change[1] != change[2]:
                parts = _split_segment_at(seg, change[0], change[1], change[2])
                if len(parts) > 1:
                    out.extend(parts)
                    stats.split += 1
                    stats.assigned += len(parts)
                    continue
            seg.speaker = speaker
            out.append(seg)
            stats.assigned += 1
        else:
            # Inconclusive: keep the segment (dropping speech is worse than an
            # unknown label) but mark it so the UI can flag it for review.
            seg.speaker = "UNKNOWN"
            out.append(seg)
            stats.unassigned += 1

    out.sort(key=lambda s: (s.start, s.end))
    stats.segments_out = len(out)
    stats.speakers = len({s.speaker for s in out if s.speaker})
    return ASRResult(
        segments=out,
        provider=asr.provider,
        language=asr.language,
        language_confidence=asr.language_confidence,
        duration_seconds=asr.duration_seconds,
        raw=asr.raw,
    ), stats

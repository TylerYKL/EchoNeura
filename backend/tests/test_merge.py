"""Speaker assignment: the step that decides whether a transcript is usable."""

from __future__ import annotations

from app.pipeline.merge import assign_speakers
from app.providers.base import (
    ASRResult,
    ASRSegment,
    ASRWord,
    DiarizationResult,
    DiarizationTurn,
)


def words(text: str, start: float, per_word: float = 0.3) -> list[ASRWord]:
    out = []
    t = start
    for w in text.split():
        out.append(ASRWord(text=w, start=round(t, 3), end=round(t + per_word, 3)))
        t += per_word + 0.05
    return out


def asr(*segments: ASRSegment) -> ASRResult:
    return ASRResult(segments=list(segments), provider="test")


def diar(*turns: DiarizationTurn) -> DiarizationResult:
    keys = {t.speaker for t in turns}
    return DiarizationResult(turns=list(turns), provider="test", speaker_count=len(keys))


def test_assigns_by_majority_time_overlap() -> None:
    result = asr(
        ASRSegment(text="early words", start=0.0, end=2.0),
        ASRSegment(text="late words", start=5.0, end=7.0),
    )
    timeline = diar(
        DiarizationTurn(speaker="A", start=0.0, end=3.0),
        DiarizationTurn(speaker="B", start=3.0, end=9.0),
    )
    merged, stats = assign_speakers(result, timeline)
    assert [s.speaker for s in merged.segments] == ["A", "B"]
    assert stats.assigned == 2
    assert stats.speakers == 2


def test_vendor_labels_are_trusted_and_not_overwritten() -> None:
    result = asr(ASRSegment(text="already labelled", start=0.0, end=2.0, speaker="VENDOR_1"))
    timeline = diar(DiarizationTurn(speaker="A", start=0.0, end=9.0))
    merged, stats = assign_speakers(result, timeline)
    assert merged.segments[0].speaker == "VENDOR_1"
    assert stats.assigned == 1


def test_no_diarization_falls_back_to_single_speaker() -> None:
    result = asr(ASRSegment(text="solo recording", start=0.0, end=2.0))
    merged, stats = assign_speakers(result, None)
    assert merged.segments[0].speaker == "S0"
    assert stats.speakers == 1


def test_empty_diarization_turns_fall_back_to_single_speaker() -> None:
    result = asr(ASRSegment(text="solo", start=0.0, end=2.0))
    merged, _ = assign_speakers(result, diar())
    assert merged.segments[0].speaker == "S0"


def test_segment_spanning_a_speaker_change_is_split() -> None:
    """One ASR line covering two speakers must become two lines.

    This is the failure mode the plan calls out: merged speakers produce garbage
    summaries downstream.
    """
    w = words("one two three four five six seven eight", 0.0, per_word=0.4)
    result = asr(ASRSegment(text=" ".join(x.text for x in w), start=0.0, end=4.0, words=w))
    timeline = diar(
        DiarizationTurn(speaker="A", start=0.0, end=1.8),
        DiarizationTurn(speaker="B", start=1.8, end=4.0),
    )
    merged, stats = assign_speakers(result, timeline)
    assert len(merged.segments) == 2, [s.speaker for s in merged.segments]
    assert {s.speaker for s in merged.segments} == {"A", "B"}
    assert stats.split == 1
    # Word timings are preserved and stay ordered.
    assert merged.segments[0].end <= merged.segments[1].start
    assert all(s.words for s in merged.segments)


def test_split_without_word_timestamps_keeps_the_segment() -> None:
    """No word timings means we cannot split safely, so never drop speech."""
    result = asr(ASRSegment(text="no word timings here", start=0.0, end=4.0, words=[]))
    timeline = diar(
        DiarizationTurn(speaker="A", start=0.0, end=2.0),
        DiarizationTurn(speaker="B", start=2.0, end=4.0),
    )
    merged, _ = assign_speakers(result, timeline)
    assert len(merged.segments) == 1
    assert merged.segments[0].speaker in {"A", "B"}


def test_inconclusive_overlap_is_marked_unknown_not_dropped() -> None:
    """A segment with no diarization coverage must survive, flagged for review."""
    result = asr(
        ASRSegment(text="covered", start=0.0, end=2.0),
        ASRSegment(text="orphaned audio", start=50.0, end=52.0),
    )
    timeline = diar(DiarizationTurn(speaker="A", start=0.0, end=3.0))
    merged, stats = assign_speakers(result, timeline)
    assert len(merged.segments) == 2
    assert merged.segments[0].speaker == "A"
    assert merged.segments[1].speaker == "UNKNOWN"
    assert stats.unassigned == 1


def test_confidence_weighting_breaks_a_tie() -> None:
    """Equal overlap durations -> the higher-confidence turn wins."""
    result = asr(ASRSegment(text="tie breaker", start=1.0, end=3.0))
    timeline = diar(
        DiarizationTurn(speaker="LOW", start=1.0, end=2.0, confidence=0.4),
        DiarizationTurn(speaker="HIGH", start=2.0, end=3.0, confidence=0.99),
    )
    merged, _ = assign_speakers(result, timeline)
    assert merged.segments[0].speaker == "HIGH"


def test_output_is_sorted_by_time() -> None:
    result = asr(
        ASRSegment(text="second", start=5.0, end=6.0),
        ASRSegment(text="first", start=0.0, end=1.0),
    )
    timeline = diar(DiarizationTurn(speaker="A", start=0.0, end=10.0))
    merged, _ = assign_speakers(result, timeline)
    assert [s.text for s in merged.segments] == ["first", "second"]


def test_three_speakers_round_trip() -> None:
    result = asr(
        ASRSegment(text="a speaks", start=0.0, end=1.5),
        ASRSegment(text="b speaks", start=2.0, end=3.5),
        ASRSegment(text="c speaks", start=4.0, end=5.5),
    )
    timeline = diar(
        DiarizationTurn(speaker="S0", start=0.0, end=1.9),
        DiarizationTurn(speaker="S1", start=1.9, end=3.9),
        DiarizationTurn(speaker="S2", start=3.9, end=6.0),
    )
    merged, stats = assign_speakers(result, timeline)
    assert [s.speaker for s in merged.segments] == ["S0", "S1", "S2"]
    assert stats.speakers == 3
    assert stats.segments_in == stats.segments_out == 3


def test_words_are_preserved_through_merge() -> None:
    w = words("keep my timings", 0.0)
    result = asr(ASRSegment(text="keep my timings", start=w[0].start, end=w[-1].end, words=w))
    merged, _ = assign_speakers(result, diar(DiarizationTurn(speaker="A", start=0.0, end=5.0)))
    assert merged.segments[0].words == w
    assert merged.all_words == w


def test_language_metadata_survives_merge() -> None:
    result = ASRResult(
        segments=[ASRSegment(text="hola", start=0.0, end=1.0)],
        provider="test",
        language="es",
        language_confidence=0.9,
        duration_seconds=1.0,
    )
    merged, _ = assign_speakers(result, None)
    assert merged.language == "es"
    assert merged.provider == "test"
    assert merged.duration_seconds == 1.0

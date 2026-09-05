"""Mock providers must be deterministic, offline and structurally realistic.

They are the default provider, so a bug here breaks CI, demos and frontend work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.mock import MockASRProvider, MockDiarizationProvider, MockEnrichmentProvider
from app.services.audio import synth_sample_wav


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return synth_sample_wav(tmp_path / "a.wav", seconds=30.0)


def test_transcript_fits_inside_the_real_duration(wav: Path) -> None:
    result = MockASRProvider().transcribe(wav, duration_seconds=30.0)
    assert result.segments
    assert result.segments[0].start >= 0.0
    assert result.segments[-1].end <= 30.0 + 0.001


def test_segments_are_time_ordered_and_non_overlapping(wav: Path) -> None:
    result = MockASRProvider().transcribe(wav, duration_seconds=30.0)
    starts = [s.start for s in result.segments]
    assert starts == sorted(starts)
    for a, b in zip(result.segments, result.segments[1:], strict=False):
        assert a.end <= b.start + 1e-6
        assert a.end > a.start


def test_word_timestamps_are_inside_their_segment(wav: Path) -> None:
    result = MockASRProvider().transcribe(wav, duration_seconds=30.0, want_words=True)
    for seg in result.segments:
        assert seg.words, "every mock segment should carry word timings"
        assert seg.words[0].start >= seg.start - 1e-6
        assert seg.words[-1].end <= seg.end + 1e-6
        assert " ".join(w.text for w in seg.words) == seg.text
        for w in seg.words:
            assert w.end >= w.start
            assert w.confidence is not None and 0.0 <= w.confidence <= 1.0


def test_words_can_be_disabled(wav: Path) -> None:
    result = MockASRProvider().transcribe(wav, duration_seconds=12.0, want_words=False)
    assert result.segments
    assert all(not s.words for s in result.segments)


def test_deterministic_for_the_same_file(wav: Path) -> None:
    a = MockASRProvider().transcribe(wav, duration_seconds=30.0, want_diarization=True)
    b = MockASRProvider().transcribe(wav, duration_seconds=30.0, want_diarization=True)
    assert [(s.start, s.end, s.text, s.speaker) for s in a.segments] == [
        (s.start, s.end, s.text, s.speaker) for s in b.segments
    ]


def test_different_files_can_differ(tmp_path: Path) -> None:
    p1 = synth_sample_wav(tmp_path / "one.wav", seconds=20.0)
    p2 = synth_sample_wav(tmp_path / "two.wav", seconds=45.0)
    r1 = MockASRProvider().transcribe(p1, duration_seconds=20.0)
    r2 = MockASRProvider().transcribe(p2, duration_seconds=45.0)
    assert len(r2.segments) > len(r1.segments)


def test_two_speakers_alternate_when_diarization_requested(wav: Path) -> None:
    result = MockASRProvider().transcribe(wav, duration_seconds=30.0, want_diarization=True)
    speakers = [s.speaker for s in result.segments]
    assert set(speakers) == {"S0", "S1"}
    assert result.has_speaker_labels


def test_no_speaker_labels_when_diarization_not_requested(wav: Path) -> None:
    result = MockASRProvider().transcribe(wav, duration_seconds=30.0, want_diarization=False)
    assert not result.has_speaker_labels


def test_language_is_echoed_or_detected(wav: Path) -> None:
    assert (
        MockASRProvider().transcribe(wav, duration_seconds=10.0, language="auto").language == "en"
    )
    assert MockASRProvider().transcribe(wav, duration_seconds=10.0, language="ta").language == "ta"


def test_short_audio_still_yields_a_segment(tmp_path: Path) -> None:
    tiny = synth_sample_wav(tmp_path / "tiny.wav", seconds=1.0)
    result = MockASRProvider().transcribe(tiny, duration_seconds=1.0)
    assert len(result.segments) >= 1
    assert result.text


def test_missing_duration_falls_back_to_file_size(tmp_path: Path) -> None:
    wav = synth_sample_wav(tmp_path / "fallback.wav", seconds=5.0)
    result = MockASRProvider().transcribe(wav)
    assert result.segments


def test_missing_file_does_not_crash(tmp_path: Path) -> None:
    result = MockASRProvider().transcribe(tmp_path / "nope.wav", duration_seconds=8.0)
    assert result.segments


def test_full_text_joins_segments(wav: Path) -> None:
    result = MockASRProvider().transcribe(wav, duration_seconds=20.0)
    assert result.text == " ".join(s.text for s in result.segments)


# --------------------------------------------------------------------------- #
# Mock diarization
# --------------------------------------------------------------------------- #
def test_diarization_turns_cover_the_timeline(wav: Path) -> None:
    result = MockDiarizationProvider().diarize(wav, duration_seconds=30.0)
    assert result.turns
    assert result.turns[0].start >= 0.0
    assert result.turns[-1].end <= 30.0 + 0.001
    for a, b in zip(result.turns, result.turns[1:], strict=False):
        assert a.end <= b.start + 1e-6
    assert result.speaker_count == 2
    assert set(result.speaker_keys) == {"S0", "S1"}


def test_diarization_honours_num_speakers(wav: Path) -> None:
    result = MockDiarizationProvider().diarize(wav, duration_seconds=30.0, num_speakers=3)
    assert result.speaker_count == 3
    assert len(result.speaker_keys) == 3


def test_diarization_is_deterministic(wav: Path) -> None:
    a = MockDiarizationProvider().diarize(wav, duration_seconds=30.0)
    b = MockDiarizationProvider().diarize(wav, duration_seconds=30.0)
    assert [(t.speaker, t.start, t.end) for t in a.turns] == [
        (t.speaker, t.start, t.end) for t in b.turns
    ]


# --------------------------------------------------------------------------- #
# Mock enrichment (M2 shape contract)
# --------------------------------------------------------------------------- #
def test_enrichment_shape() -> None:
    provider = MockEnrichmentProvider()
    text = "First sentence. Second sentence. Third sentence."
    out = provider.enrich(text)
    assert out.provider == "mock"
    assert out.summary
    assert 1 <= len(out.key_points) <= 5
    assert out.quotes
    for q in out.quotes:
        assert {"text", "speaker", "start", "end"} <= set(q)


def test_enrichment_respects_limit() -> None:
    provider = MockEnrichmentProvider()
    text = ". ".join(f"Sentence {i}" for i in range(20))
    assert len(provider.quotes(text, limit=3)) == 3
    assert len(provider.key_points(text, limit=2)) == 2


def test_enrichment_handles_empty_transcript() -> None:
    provider = MockEnrichmentProvider()
    assert provider.summarize("")
    assert provider.quotes("") == []

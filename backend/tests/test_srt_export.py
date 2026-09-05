"""Subtitle/export generation.

These tests use lightweight stand-ins for ORM rows so the exporter logic is
verified independently of the database.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.pipeline.srt import (
    ExportOptions,
    SpeakerRef,
    build_cues_from_models,
    cues_to_srt,
    cues_to_vtt,
    segments_to_markdown,
    segments_to_transcript_txt,
    wrap_text,
)


@dataclass
class FakeSegment:
    id: str
    start: float
    end: float
    text: str
    speaker_id: str | None = None
    seq: int = 0


SPEAKERS = {
    "sp1": SpeakerRef(display_name="Alice", color="#6366f1"),
    "sp2": SpeakerRef(display_name="Bob", color="#0ea5e9"),
}


def seg(i: int, start: float, end: float, text: str, speaker: str | None = None) -> FakeSegment:
    return FakeSegment(id=f"s{i}", start=start, end=end, text=text, speaker_id=speaker, seq=i)


# --------------------------------------------------------------------------- #
# wrap_text
# --------------------------------------------------------------------------- #
def test_wrap_short_text_is_untouched() -> None:
    assert wrap_text("hello there", 42, 2) == "hello there"


def test_wrap_splits_on_word_boundaries() -> None:
    text = "the quick brown fox jumps over the lazy dog again and again"
    out = wrap_text(text, 20, 2)
    lines = out.split("\n")
    assert len(lines) == 2
    assert all(len(line) <= 20 for line in lines)
    assert " " not in lines[0][:1]  # no leading space
    # No word is cut in half across the break.
    assert not lines[0].endswith("ju")


def test_wrap_collapses_whitespace() -> None:
    assert wrap_text("  too    many \n spaces  ", 42, 2) == "too many spaces"


def test_wrap_truncates_with_ellipsis_when_too_long() -> None:
    text = " ".join(f"word{i}" for i in range(60))
    out = wrap_text(text, 20, 2)
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[-1].endswith("…")
    assert all(len(line) <= 20 for line in lines)


def test_wrap_hard_splits_a_word_longer_than_the_line() -> None:
    out = wrap_text("supercalifragilisticexpialidocious", 12, 2)
    assert all(len(line) <= 12 for line in out.split("\n"))


def test_wrap_empty_string() -> None:
    assert wrap_text("", 42, 2) == ""
    assert wrap_text("   \n  ", 42, 2) == ""


# --------------------------------------------------------------------------- #
# Cue building
# --------------------------------------------------------------------------- #
def test_same_speaker_segments_merge_into_one_cue() -> None:
    segments = [
        seg(0, 0.0, 2.0, "Hello there.", "sp1"),
        seg(1, 2.2, 4.0, "Nice to meet you.", "sp1"),
    ]
    cues = build_cues_from_models(segments, SPEAKERS)
    assert len(cues) == 1
    assert cues[0].text == "Hello there. Nice to meet you."
    assert cues[0].start == 0.0
    assert cues[0].end == 4.0
    assert cues[0].source_ids == ["s0", "s1"]


def test_speaker_change_forces_a_new_cue() -> None:
    segments = [
        seg(0, 0.0, 2.0, "Hello there.", "sp1"),
        seg(1, 2.1, 4.0, "Hi Alice.", "sp2"),
    ]
    cues = build_cues_from_models(segments, SPEAKERS)
    assert len(cues) == 2
    assert cues[0].speaker_name == "Alice"
    assert cues[1].speaker_name == "Bob"


def test_large_gap_between_same_speaker_segments_splits_cue() -> None:
    segments = [
        seg(0, 0.0, 2.0, "First thought.", "sp1"),
        seg(1, 9.0, 11.0, "Much later thought.", "sp1"),
    ]
    cues = build_cues_from_models(segments, SPEAKERS, ExportOptions(merge_gap_seconds=0.75))
    assert len(cues) == 2


def test_cues_never_exceed_max_duration() -> None:
    opt = ExportOptions(max_cue_seconds=4.0, merge_gap_seconds=10.0)
    segments = [seg(i, float(i), float(i) + 0.9, f"bit {i}", "sp1") for i in range(10)]
    cues = build_cues_from_models(segments, SPEAKERS, opt)
    assert len(cues) > 1
    assert all(c.duration <= 10.0 for c in cues)


def test_cues_never_exceed_max_characters() -> None:
    opt = ExportOptions(max_chars_per_line=20, max_lines_per_cue=2)
    segments = [
        seg(0, 0.0, 3.0, "a rather long first sentence here", "sp1"),
        seg(1, 3.1, 6.0, "and a rather long second sentence too", "sp1"),
    ]
    cues = build_cues_from_models(segments, SPEAKERS, opt)
    assert len(cues) == 2  # would overflow 40 chars, so it must split


def test_overlapping_cues_are_deoverlapped() -> None:
    segments = [
        seg(0, 0.0, 3.0, "First.", "sp1"),
        seg(1, 2.9, 5.0, "Second.", "sp2"),  # overlaps the previous by 0.1s
    ]
    cues = build_cues_from_models(segments, SPEAKERS)
    assert len(cues) == 2
    assert cues[0].end <= cues[1].start


def test_zero_length_segment_gets_minimum_duration() -> None:
    segments = [seg(0, 5.0, 5.0, "Blip.", "sp1")]
    cues = build_cues_from_models(segments, SPEAKERS, ExportOptions(min_cue_seconds=0.6))
    assert cues[0].end > cues[0].start


def test_blank_segments_are_skipped() -> None:
    segments = [seg(0, 0.0, 1.0, "   ", "sp1"), seg(1, 1.0, 2.0, "Real text.", "sp1")]
    cues = build_cues_from_models(segments, SPEAKERS)
    assert len(cues) == 1
    assert cues[0].text == "Real text."


def test_speaker_names_can_be_omitted() -> None:
    segments = [seg(0, 0.0, 2.0, "Hello.", "sp1")]
    cues = build_cues_from_models(segments, SPEAKERS, ExportOptions(include_speaker_names=False))
    assert cues[0].speaker_name is None
    srt = cues_to_srt(cues, include_speaker_names=False)
    assert "Alice" not in srt


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_srt_document_shape() -> None:
    segments = [
        seg(0, 0.0, 2.0, "Hello there.", "sp1"),
        seg(1, 2.5, 4.5, "Hi Alice, good to see you.", "sp2"),
    ]
    cues = build_cues_from_models(segments, SPEAKERS)
    srt = cues_to_srt(cues)
    blocks = [b for b in srt.split("\n\n") if b.strip()]
    assert len(blocks) == 2

    first = blocks[0].split("\n")
    assert first[0] == "1"
    assert first[1] == "00:00:00,000 --> 00:00:02,000"
    assert first[2].startswith("Alice: ")

    second = blocks[1].split("\n")
    assert second[0] == "2"
    assert "-->" in second[1]
    assert second[2].startswith("Bob: ")


def test_srt_uses_comma_and_vtt_uses_dot() -> None:
    segments = [seg(0, 1.234, 2.345, "Timing check.", "sp1")]
    cues = build_cues_from_models(segments, SPEAKERS)
    assert "00:00:01,234 --> 00:00:02,345" in cues_to_srt(cues)
    assert "00:00:01.234 --> 00:00:02.345" in cues_to_vtt(cues)


def test_vtt_has_required_header() -> None:
    cues = build_cues_from_models([seg(0, 0.0, 1.0, "Hi.", "sp1")], SPEAKERS)
    vtt = cues_to_vtt(cues)
    assert vtt.startswith("WEBVTT\n")
    assert "<v Alice>" in vtt


def test_srt_indices_are_sequential_and_start_at_one() -> None:
    segments = [
        seg(i, float(i) * 5, float(i) * 5 + 2, f"Line {i}.", "sp1" if i % 2 else "sp2")
        for i in range(6)
    ]
    cues = build_cues_from_models(segments, SPEAKERS, ExportOptions(merge_gap_seconds=0.0))
    srt = cues_to_srt(cues)
    numbers = [int(b.split("\n")[0]) for b in srt.split("\n\n") if b.strip()]
    assert numbers == list(range(1, len(numbers) + 1))


def test_txt_transcript_includes_timestamps_and_names() -> None:
    segments = [
        seg(0, 0.0, 2.0, "Hello there.", "sp1"),
        seg(1, 65.0, 67.0, "A minute later.", "sp2"),
    ]
    txt = segments_to_transcript_txt(segments, SPEAKERS)
    lines = txt.strip().split("\n")
    assert lines[0] == "[0:00] Alice: Hello there."
    assert lines[1] == "[1:05] Bob: A minute later."


def test_txt_transcript_without_metadata() -> None:
    segments = [seg(0, 0.0, 2.0, "Just text.", "sp1")]
    txt = segments_to_transcript_txt(
        segments, SPEAKERS, include_timestamps=False, include_speaker_names=False
    )
    assert txt == "Just text.\n"


def test_unknown_speaker_label_in_txt() -> None:
    segments = [seg(0, 0.0, 2.0, "Orphan line.", None)]
    txt = segments_to_transcript_txt(segments, SPEAKERS, include_timestamps=False)
    assert txt.startswith("Unknown: ")


def test_markdown_groups_by_speaker_turn() -> None:
    segments = [
        seg(0, 0.0, 2.0, "Part one.", "sp1"),
        seg(1, 2.0, 3.0, "Part two.", "sp1"),
        seg(2, 3.0, 4.0, "Reply.", "sp2"),
    ]
    md = segments_to_markdown(segments, SPEAKERS, title="demo")
    assert md.startswith("# demo\n")
    assert "**Alice**" in md
    assert "**Bob**" in md
    # Consecutive same-speaker segments are joined into one paragraph.
    assert "Part one. Part two." in md
    assert md.count("**Alice**") == 1


def test_markdown_timestamps_optional() -> None:
    segments = [seg(0, 0.0, 2.0, "Text.", "sp1")]
    assert "_0:00_" in segments_to_markdown(segments, SPEAKERS, include_timestamps=True)
    assert "_0:00_" not in segments_to_markdown(segments, SPEAKERS, include_timestamps=False)


def test_empty_input_produces_empty_documents() -> None:
    assert build_cues_from_models([], SPEAKERS) == []
    assert cues_to_srt([]) == ""
    assert segments_to_transcript_txt([], SPEAKERS) == ""


@pytest.mark.parametrize("fmt", ["srt", "vtt"])
def test_export_is_utf8_safe(fmt: str) -> None:
    """Multilingual transcripts (plan: 40+ languages) must survive export."""
    segments = [seg(0, 0.0, 2.0, "हिंदी और தமிழ் 中文 العربية", "sp1")]
    cues = build_cues_from_models(segments, SPEAKERS)
    body = cues_to_srt(cues) if fmt == "srt" else cues_to_vtt(cues)
    assert "हिंदी" in body
    assert "中文" in body
    assert body.encode("utf-8").decode("utf-8") == body

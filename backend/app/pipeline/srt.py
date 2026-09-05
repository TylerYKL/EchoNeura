"""Subtitle and transcript exporters (M1 deliverable: "SRT export").

Supports SRT, WebVTT and plain text, all generated from the *current* (possibly
human-edited) segments so re-exporting after corrections just works.

Subtitle-craft rules implemented here, because raw ASR segments make bad cues:
  * cues are capped at ~2 lines and ~42 chars/line (broadcast-safe),
  * consecutive segments from the same speaker are merged up to a max duration,
  * a cue always changes at a speaker change,
  * timings never overlap — the next cue starts at least 1ms after the previous,
  * long unbroken text is wrapped on word boundaries, not mid-word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.pipeline.timefmt import format_clock, format_srt_timestamp, format_vtt_timestamp

WHITESPACE_RE = re.compile(r"\s+")
# Break a cue line preferentially after punctuation.
PREFERRED_BREAKS = (",", ";", ":", ".", "?", "!")


class CueSource(Protocol):
    """Minimal shape needed to build subtitles (matches models.Segment)."""

    @property
    def start(self) -> float: ...

    @property
    def end(self) -> float: ...

    @property
    def text(self) -> str: ...


@dataclass(slots=True)
class SpeakerRef:
    display_name: str
    color: str | None = None


@dataclass(slots=True)
class Cue:
    index: int
    start: float
    end: float
    text: str
    speaker_name: str | None = None
    speaker_color: str | None = None
    source_ids: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(slots=True)
class ExportOptions:
    include_speaker_names: bool = True
    max_chars_per_line: int = 42
    max_lines_per_cue: int = 2
    max_cue_seconds: float = 7.0
    min_cue_seconds: float = 0.6
    merge_gap_seconds: float = 0.75
    keep_min_gap_ms: int = 1


def wrap_text(text: str, max_chars_per_line: int, max_lines: int) -> str:
    """Greedy word wrap to at most `max_lines` lines joined by '\\n'.

    If the text cannot fit, it is truncated with an ellipsis — an over-long cue
    that scrolls off screen is worse than one that is trimmed, and the full text
    always remains available in the transcript view and TXT export.
    """
    words = WHITESPACE_RE.sub(" ", (text or "").strip()).split(" ")
    words = [w for w in words if w]
    if not words:
        return ""

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars_per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = word
        else:
            # Single word longer than the line limit: hard-split it.
            while len(word) > max_chars_per_line:
                cut = max_chars_per_line
                # Prefer breaking after punctuation if one is near the limit.
                for punct in PREFERRED_BREAKS:
                    idx = word.rfind(punct, max_chars_per_line // 2, max_chars_per_line + 1)
                    if idx != -1:
                        cut = idx + 1
                        break
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) < max_lines:
        return "\n".join(lines)

    # Overflow: signal truncation on the final line.
    joined = lines[:max_lines]
    last = joined[-1]
    budget = max_chars_per_line
    used = sum(len(line.split()) for line in joined)
    remaining_words = words[used:]
    if remaining_words:
        last = (last[: max(0, budget - 1)].rstrip() + "…") if last else "…"
        joined[-1] = last
    return "\n".join(joined)


def build_cues(
    segments: list[tuple[CueSource, str | None, str | None]],
    options: ExportOptions | None = None,
) -> list[Cue]:
    """Turn (segment, speaker_id, segment_id) triples into subtitle cues.

    The triple form keeps this function decoupled from SQLAlchemy while still
    allowing speaker lookup and source-id tracing.
    """
    opt = options or ExportOptions()
    cues: list[Cue] = []
    max_chars = opt.max_chars_per_line * opt.max_lines_per_cue

    for seg, speaker_id, seg_id in segments:
        text = WHITESPACE_RE.sub(" ", (seg.text or "").strip())
        if not text:
            continue
        start, end = float(seg.start), float(seg.end)
        if end <= start:
            end = start + opt.min_cue_seconds

        if cues:
            prev = cues[-1]
            same_speaker = prev.speaker_name == speaker_id
            gap = start - prev.end
            would_overflow = (
                len(prev.text.replace("\n", " ")) + len(text) + 1 > max_chars
                or prev.duration + gap + (end - start) > opt.max_cue_seconds
            )
            if same_speaker and 0 <= gap <= opt.merge_gap_seconds and not would_overflow:
                prev.text = f"{prev.text} {text}".strip()
                prev.end = end
                prev.source_ids.append(seg_id or "")
                continue

        cues.append(
            Cue(
                index=len(cues) + 1,
                start=start,
                end=end,
                text=text,
                speaker_name=speaker_id,
                source_ids=[seg_id or ""],
            )
        )

    # Resolve speaker ids -> display names, then wrap and de-overlap.
    resolved: list[Cue] = []
    for cue in cues:
        if resolved:
            prev = resolved[-1]
            min_end = prev.start + opt.min_cue_seconds
            if prev.end > cue.start - (opt.keep_min_gap_ms / 1000):
                prev.end = max(min_end, cue.start - (opt.keep_min_gap_ms / 1000))
        resolved.append(cue)

    return resolved


def build_cues_from_models(
    segments: list,
    speakers: dict[str, SpeakerRef],
    options: ExportOptions | None = None,
) -> list[Cue]:
    """Convenience wrapper that accepts ORM Segment rows."""
    opt = options or ExportOptions()
    triples: list[tuple[CueSource, str | None, str | None]] = []
    for seg in segments:
        ref = (
            speakers.get(seg.speaker_id) if (opt.include_speaker_names and seg.speaker_id) else None
        )
        triples.append((seg, ref.display_name if ref else None, seg.id))

    cues = build_cues(triples, opt)
    if opt.include_speaker_names:
        color_by_name = {ref.display_name: ref.color for ref in speakers.values()}
        for cue in cues:
            cue.speaker_color = color_by_name.get(cue.speaker_name or "")
    return cues


def cues_to_srt(
    cues: list[Cue],
    *,
    include_speaker_names: bool = True,
    max_chars_per_line: int = 42,
    max_lines_per_cue: int = 2,
) -> str:
    """Render cues as an SRT document."""
    blocks: list[str] = []
    for i, cue in enumerate(cues, start=1):
        body = wrap_text(cue.text, max_chars_per_line, max_lines_per_cue)
        if not body:
            continue
        if include_speaker_names and cue.speaker_name:
            body = f"{cue.speaker_name}: {body}"
            body = wrap_text(body, max_chars_per_line, max_lines_per_cue)
        start = format_srt_timestamp(cue.start)
        end = format_srt_timestamp(max(cue.end, cue.start + 0.001))
        blocks.append(f"{i}\n{start} --> {end}\n{body}\n")
    return "\n".join(blocks)


def cues_to_vtt(
    cues: list[Cue],
    *,
    include_speaker_names: bool = True,
    max_chars_per_line: int = 42,
    max_lines_per_cue: int = 2,
) -> str:
    """Render cues as WebVTT (uses '.' as the millisecond separator)."""
    out = ["WEBVTT", ""]
    n = 0
    for cue in cues:
        body = wrap_text(cue.text, max_chars_per_line, max_lines_per_cue)
        if not body:
            continue
        if include_speaker_names and cue.speaker_name:
            out.append(f"<v {cue.speaker_name}>")
            body = wrap_text(body, max_chars_per_line, max_lines_per_cue)
        n += 1
        start = format_vtt_timestamp(cue.start)
        end = format_vtt_timestamp(max(cue.end, cue.start + 0.001))
        out.append(f"{n}")
        out.append(f"{start} --> {end}")
        out.append(body)
        out.append("")
    return "\n".join(out)


def segments_to_transcript_txt(
    segments: list,
    speakers: dict[str, SpeakerRef],
    *,
    include_timestamps: bool = True,
    include_speaker_names: bool = True,
) -> str:
    """Plain-text transcript, one line per segment: [00:12] Alice: text"""
    lines: list[str] = []
    for seg in segments:
        text = WHITESPACE_RE.sub(" ", (seg.text or "").strip())
        if not text:
            continue
        prefix = ""
        if include_timestamps:
            prefix += f"[{format_clock(seg.start)}] "
        if include_speaker_names:
            ref = speakers.get(seg.speaker_id) if seg.speaker_id else None
            name = ref.display_name if ref else "Unknown"
            prefix += f"{name}: "
        lines.append(f"{prefix}{text}".strip())
    return "\n".join(lines) + ("\n" if lines else "")


def segments_to_markdown(
    segments: list,
    speakers: dict[str, SpeakerRef],
    *,
    title: str | None = None,
    include_timestamps: bool = False,
) -> str:
    """Markdown transcript grouped by speaker turn (M2 export target)."""
    out: list[str] = []
    if title:
        out += [f"# {title}", ""]
    last_speaker: str | None = object()
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            out.append(" ".join(buffer).strip())
            out.append("")
            buffer.clear()

    for seg in segments:
        text = WHITESPACE_RE.sub(" ", (seg.text or "").strip())
        if not text:
            continue
        ref = speakers.get(seg.speaker_id) if seg.speaker_id else None
        name = ref.display_name if ref else "Unknown"
        if name != last_speaker:
            flush()
            stamp = f" _{format_clock(seg.start)}_" if include_timestamps else ""
            out.append(f"**{name}**{stamp}")
            last_speaker = name
        buffer.append(text)
    flush()
    return "\n".join(out).rstrip() + "\n"

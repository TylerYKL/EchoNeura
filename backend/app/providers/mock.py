"""Offline, deterministic providers used for demos, CI and frontend development.

The mock ASR is *not* a stub that returns nothing — it derives a plausible,
stable transcript from the real audio duration (measured with ffmpeg), with two
speakers, word-level timestamps and per-word confidences. That means every
downstream code path (speaker merge, SRT export, inline editing, pagination)
is genuinely exercised with zero API cost.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

from app.providers.base import (
    ASRProvider,
    ASRResult,
    ASRSegment,
    ASRWord,
    DiarizationProvider,
    DiarizationResult,
    DiarizationTurn,
    EnrichmentProvider,
    EnrichmentResult,
)

# A conversation-ish script so the demo UI looks real rather than "lorem ipsum".
_SCRIPT: list[tuple[str, str]] = [
    ("S0", "So the goal here is to turn a raw recording into something people actually read."),
    ("S1", "Right. Most teams stop at the transcript, but the transcript is not the deliverable."),
    ("S0", "The deliverable is the summary, the quotes, maybe a short clip for social."),
    (
        "S1",
        "And that only works if the transcript is clean. Speaker labels matter more than people expect.",
    ),
    (
        "S0",
        "Agreed. If two people are merged into one paragraph, every downstream summary is wrong.",
    ),
    (
        "S1",
        "So we fix diarization first, then layer generation on top of a trustworthy transcript.",
    ),
    ("S0", "That is also why we let humans edit inline. The model gets you ninety percent."),
    ("S1", "The last ten percent is names, jargon, and acronyms the model has never heard."),
    ("S0", "Export matters too. SRT for video, plain text for the newsletter, markdown for docs."),
    (
        "S1",
        "One transcript, several formats, no re-typing. That is the whole product in a sentence.",
    ),
]

_WORD_SECONDS = 0.26
_GAP_SECONDS = 0.45


def _seed_for(audio_path: Path) -> int:
    """Stable seed from filename+size so re-runs give identical output."""
    try:
        size = audio_path.stat().st_size
    except OSError:
        size = 0
    digest = hashlib.sha256(f"{audio_path.name}:{size}".encode()).hexdigest()
    return int(digest[:12], 16)


class MockASRProvider(ASRProvider):
    name = "mock"
    supports_diarization = True
    supports_word_timestamps = True

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "auto",
        duration_seconds: float | None = None,
        want_diarization: bool = False,
        want_words: bool = True,
    ) -> ASRResult:
        duration = float(duration_seconds or 0.0)
        if duration <= 0 and audio_path.exists():
            duration = max(6.0, audio_path.stat().st_size / 32_000)
        duration = max(duration, 4.0)

        rng = random.Random(_seed_for(audio_path))
        segments: list[ASRSegment] = []
        cursor = 0.35
        idx = 0

        while cursor < duration - 0.6:
            speaker, line = _SCRIPT[idx % len(_SCRIPT)]
            words_in = line.split()
            # Only keep as many words as fit in the remaining audio.
            remaining = duration - cursor - 0.2
            max_words = max(1, int(remaining / (_WORD_SECONDS + 0.02)))
            words_in = words_in[:max_words]

            words: list[ASRWord] = []
            t = cursor
            for w in words_in:
                wlen = _WORD_SECONDS * (0.7 + 0.06 * min(len(w), 10))
                start, end = round(t, 3), round(t + wlen, 3)
                words.append(
                    ASRWord(
                        text=w,
                        start=start,
                        end=end,
                        confidence=round(rng.uniform(0.86, 0.995), 3),
                    )
                )
                t = end + rng.uniform(0.01, 0.05)

            seg_start = words[0].start
            seg_end = words[-1].end
            text = " ".join(w.text for w in words)
            conf = round(sum(w.confidence or 0 for w in words) / len(words), 3)
            segments.append(
                ASRSegment(
                    text=text,
                    start=seg_start,
                    end=seg_end,
                    speaker=speaker if want_diarization else None,
                    confidence=conf,
                    words=words if want_words else [],
                )
            )
            cursor = seg_end + _GAP_SECONDS + rng.uniform(0.0, 0.5)
            idx += 1
            if idx > 400:  # hard stop for pathological durations
                break

        if not segments:
            segments.append(
                ASRSegment(
                    text="No speech detected.",
                    start=0.0,
                    end=min(duration, 1.5),
                    speaker="S0" if want_diarization else None,
                    confidence=0.0,
                    words=[],
                )
            )

        return ASRResult(
            segments=segments,
            provider=self.name,
            language="en" if language in ("auto", None) else language,
            language_confidence=0.99,
            duration_seconds=duration,
            raw={"mock": True, "script_lines": len(segments)},
        )


class MockDiarizationProvider(DiarizationProvider):
    name = "mock"

    def diarize(
        self,
        audio_path: Path,
        *,
        duration_seconds: float | None = None,
        num_speakers: int | None = None,
    ) -> DiarizationResult:
        duration = float(duration_seconds or 30.0)
        # Same key format as MockASRProvider ("S0", "S1", ...) so the two mocks
        # can be used together in the split ASR + diarization path.
        speakers = [f"S{i}" for i in range(num_speakers or 2)]
        turns: list[DiarizationTurn] = []
        rng = random.Random(_seed_for(audio_path) + 7)
        t = 0.35
        i = 0
        while t < duration - 0.5:
            span = min(rng.uniform(3.0, 6.5), duration - t - 0.2)
            if span <= 0.2:
                break
            turns.append(
                DiarizationTurn(
                    speaker=speakers[i % len(speakers)],
                    start=round(t, 3),
                    end=round(t + span, 3),
                    confidence=round(rng.uniform(0.8, 0.99), 3),
                )
            )
            t += span + rng.uniform(0.2, 0.6)
            i += 1
        return DiarizationResult(
            turns=turns, provider=self.name, speaker_count=len(speakers), raw={"mock": True}
        )


class MockEnrichmentProvider(EnrichmentProvider):
    name = "mock"

    def summarize(self, transcript_text: str, *, language: str | None = None) -> str:
        words = transcript_text.split()
        head = " ".join(words[:28])
        return (
            f"[mock summary] This {len(words)}-word recording covers {head}... "
            "The speakers agree that a clean, diarized transcript is the prerequisite "
            "for useful summaries, quotes and exports."
        )

    def key_points(
        self, transcript_text: str, *, language: str | None = None, limit: int = 5
    ) -> list[str]:
        base = [
            "The transcript is an intermediate artifact, not the deliverable.",
            "Diarization errors propagate into every downstream summary.",
            "Human inline editing covers the last ten percent (names, jargon).",
            "One transcript should export to SRT, TXT, MD and JSON.",
            "Quote cards are generated from human-approved segments only.",
        ]
        return base[:limit]

    def quotes(
        self, transcript_text: str, *, language: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        sentences = [s.strip() for s in transcript_text.replace("\n", " ").split(".") if s.strip()]
        out: list[dict[str, Any]] = []
        for i, s in enumerate(sentences[:limit]):
            out.append(
                {
                    "text": (s + ".").strip(),
                    "speaker": f"Speaker {1 + (i % 2)}",
                    "start": round(i * 4.0, 2),
                    "end": round(i * 4.0 + 3.5, 2),
                    "reason": "mock: selected by position",
                }
            )
        return out

    def enrich(self, transcript_text: str, *, language: str | None = None) -> EnrichmentResult:
        return EnrichmentResult(
            provider=self.name,
            summary=self.summarize(transcript_text, language=language),
            key_points=self.key_points(transcript_text, language=language),
            quotes=self.quotes(transcript_text, language=language),
        )

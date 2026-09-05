"""Provider interfaces — the seam that makes the AI stage swappable.

Three implementations sit behind every interface:

    mock      deterministic, offline, zero cost  -> demos, CI, frontend work
    local     faster-whisper / pyannote on your own GPU box
    api       AssemblyAI / Deepgram / Groq / OpenAI / Anthropic

Nothing outside `app/providers` may import a concrete vendor SDK or build a
vendor HTTP payload. The pipeline only ever sees `ASRResult` / `DiarizationResult`.

Two shapes of ASR exist in the real world and both are supported:

 1. Monolithic — the vendor does ASR *and* diarization in one call
    (AssemblyAI, Deepgram). Those providers set `supports_diarization = True`
    and return segments that already carry `speaker`.
 2. Split — ASR (faster-whisper, Groq) plus a separate diarization pass
    (pyannote). `app.pipeline.merge` then assigns speakers to words by
    time-overlap.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ProviderError(RuntimeError):
    """Base class for provider failures. Message is shown to the operator."""

    #: whether the worker should retry the job after backing off
    retryable: bool = True


class ProviderUnavailable(ProviderError):
    """The provider is selected but its prerequisites (key, dep, quota) are missing.

    NOT retryable: re-running will fail identically until configuration changes.
    Carries an actionable message because it is surfaced straight to the
    operator in the job error field.
    """

    retryable = False


@dataclass(slots=True)
class ASRWord:
    text: str
    start: float
    end: float
    confidence: float | None = None


@dataclass(slots=True)
class ASRSegment:
    text: str
    start: float
    end: float
    speaker: str | None = None
    confidence: float | None = None
    words: list[ASRWord] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(slots=True)
class ASRResult:
    segments: list[ASRSegment]
    provider: str
    language: str | None = None
    language_confidence: float | None = None
    duration_seconds: float | None = None
    raw: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def has_speaker_labels(self) -> bool:
        return any(s.speaker for s in self.segments)

    @property
    def all_words(self) -> list[ASRWord]:
        return [w for s in self.segments for w in s.words]


@dataclass(slots=True)
class DiarizationTurn:
    speaker: str
    start: float
    end: float
    confidence: float | None = None


@dataclass(slots=True)
class DiarizationResult:
    turns: list[DiarizationTurn]
    provider: str
    speaker_count: int | None = None
    raw: dict[str, Any] | None = None

    @property
    def speaker_keys(self) -> list[str]:
        seen: dict[str, None] = {}
        for t in self.turns:
            seen.setdefault(t.speaker, None)
        return list(seen)


@dataclass(slots=True)
class EnrichmentResult:
    provider: str
    summary: str | None = None
    key_points: list[str] = field(default_factory=list)
    quotes: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class ASRProvider(ABC):
    """Speech-to-text, optionally with speaker labels."""

    name: str = "abstract"
    supports_diarization: bool = False
    supports_word_timestamps: bool = True

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "auto",
        duration_seconds: float | None = None,
        want_diarization: bool = False,
        want_words: bool = True,
    ) -> ASRResult: ...

    def check_available(self) -> None:
        """Raise ProviderUnavailable if prerequisites (keys, deps) are missing."""
        return None


class DiarizationProvider(ABC):
    """Who spoke when — independent of what they said."""

    name: str = "abstract"

    @abstractmethod
    def diarize(
        self,
        audio_path: Path,
        *,
        duration_seconds: float | None = None,
        num_speakers: int | None = None,
    ) -> DiarizationResult: ...

    def check_available(self) -> None:
        return None


class EnrichmentProvider(ABC):
    """Summary + key points + pull-quotes from a finished transcript (M2/M3)."""

    name: str = "abstract"

    @abstractmethod
    def summarize(self, transcript_text: str, *, language: str | None = None) -> str: ...

    @abstractmethod
    def key_points(
        self, transcript_text: str, *, language: str | None = None, limit: int = 5
    ) -> list[str]: ...

    @abstractmethod
    def quotes(
        self, transcript_text: str, *, language: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]: ...

    def check_available(self) -> None:
        return None

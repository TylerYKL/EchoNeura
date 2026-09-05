"""Self-hosted providers: faster-whisper (ASR) + pyannote (diarization).

These are real implementations, not placeholders. They are *lazily imported* so
the app boots on a machine without torch — the dependency is only touched when
you actually select the provider, and the failure message tells you exactly what
to install.

Enable with:
    pip install faster-whisper torch pyannote.audio
    ECHONEURA_ASR_PROVIDER=faster_whisper
    ECHONEURA_DIARIZATION_PROVIDER=pyannote
    ECHONEURA_HUGGINGFACE_TOKEN=hf_xxx   # pyannote/speaker-diarization-3.1 is gated
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.providers.base import (
    ASRProvider,
    ASRResult,
    ASRSegment,
    ASRWord,
    DiarizationProvider,
    DiarizationResult,
    DiarizationTurn,
    ProviderUnavailable,
)

logger = logging.getLogger(__name__)


def _resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001 - torch missing/broken must degrade to CPU, not crash
        return "cpu"


class FasterWhisperProvider(ASRProvider):
    """Local Whisper via CTranslate2. CPU int8 or GPU float16."""

    name = "faster_whisper"
    supports_diarization = False  # pair with pyannote
    supports_word_timestamps = True

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        self.model_size = model_size or settings.whisper_model_size
        self.device = _resolve_device(device or settings.whisper_device)
        self.compute_type = compute_type or settings.whisper_compute_type
        self._model: Any = None

    def check_available(self) -> None:
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise ProviderUnavailable(
                "faster-whisper is not installed. Run "
                "`pip install faster-whisper` (plus torch if you want pyannote diarization), "
                "or set ECHONEURA_ASR_PROVIDER=mock|assemblyai|deepgram|groq_whisper."
            ) from exc

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        self.check_available()
        from faster_whisper import WhisperModel

        logger.info(
            "Loading faster-whisper model=%s device=%s compute=%s",
            self.model_size,
            self.device,
            self.compute_type,
        )
        self._model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "auto",
        duration_seconds: float | None = None,
        want_diarization: bool = False,
        want_words: bool = True,
    ) -> ASRResult:
        model = self._load_model()
        kwargs: dict[str, Any] = {
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": want_words,
        }
        if language and language != "auto":
            kwargs["language"] = language

        seg_iter, info = model.transcribe(str(audio_path), **kwargs)

        segments: list[ASRSegment] = []
        for s in seg_iter:
            words = [
                ASRWord(
                    text=(w.word or "").strip(),
                    start=float(w.start or 0.0),
                    end=float(w.end or 0.0),
                    confidence=float(w.probability) if w.probability is not None else None,
                )
                for w in (s.words or [])
                if (w.word or "").strip()
            ]
            segments.append(
                ASRSegment(
                    text=(s.text or "").strip(),
                    start=float(s.start or 0.0),
                    end=float(s.end or 0.0),
                    speaker=None,  # filled in by the merge stage
                    confidence=float(s.avg_logprob) if s.avg_logprob is not None else None,
                    words=words,
                )
            )

        return ASRResult(
            segments=segments,
            provider=f"{self.name}:{self.model_size}",
            language=getattr(info, "language", None),
            language_confidence=getattr(info, "language_probability", None),
            duration_seconds=float(getattr(info, "duration", duration_seconds) or 0.0) or None,
            raw={"device": self.device, "compute_type": self.compute_type},
        )


class PyannoteDiarizationProvider(DiarizationProvider):
    """pyannote/speaker-diarization-3.1 — requires a HuggingFace token + accepting
    the model's user conditions on the Hub."""

    name = "pyannote"

    def __init__(self, model_id: str | None = None, token: str | None = None) -> None:
        self.model_id = model_id or settings.pyannote_diarization_model
        self.token = token or settings.huggingface_token
        self._pipeline: Any = None

    def check_available(self) -> None:
        if not self.token:
            raise ProviderUnavailable(
                "pyannote needs ECHONEURA_HUGGINGFACE_TOKEN, and you must accept the "
                f"conditions for {self.model_id} on the HuggingFace Hub. "
                "Alternatively use a vendor that bundles diarization "
                "(ECHONEURA_ASR_PROVIDER=assemblyai|deepgram)."
            )
        try:
            import pyannote.audio  # noqa: F401
        except ImportError as exc:
            raise ProviderUnavailable(
                "pyannote.audio is not installed. Run `pip install torch pyannote.audio`, "
                "or set ECHONEURA_DIARIZATION_PROVIDER=mock|none."
            ) from exc

    def _load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        self.check_available()
        from pyannote.audio import Pipeline

        device = _resolve_device("auto")
        pipeline = Pipeline.from_pretrained(self.model_id, use_auth_token=self.token)
        if pipeline is None:
            raise ProviderUnavailable(
                f"Could not load {self.model_id}. Check that your token has access "
                "and that you accepted the model conditions."
            )
        try:
            import torch

            pipeline.to(torch.device(device))
        except Exception:  # noqa: BLE001  # pragma: no cover - CPU fallback
            logger.warning("Could not move pyannote pipeline to %s; continuing.", device)
        self._pipeline = pipeline
        return pipeline

    def diarize(
        self,
        audio_path: Path,
        *,
        duration_seconds: float | None = None,
        num_speakers: int | None = None,
    ) -> DiarizationResult:
        pipeline = self._load()
        kwargs: dict[str, Any] = {}
        if num_speakers:
            kwargs["num_speakers"] = num_speakers
        annotation = pipeline(str(audio_path), **kwargs)

        turns: list[DiarizationTurn] = []
        speakers: set[str] = set()
        for turn, _track, speaker in annotation.itertracks(yield_label=True):
            key = str(speaker)
            speakers.add(key)
            turns.append(
                DiarizationTurn(
                    speaker=key,
                    start=float(turn.start),
                    end=float(turn.end),
                )
            )
        turns.sort(key=lambda t: (t.start, t.end))
        return DiarizationResult(
            turns=turns,
            provider=self.name,
            speaker_count=len(speakers),
            raw={"model": self.model_id},
        )

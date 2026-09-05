"""Provider registry — resolves env-configured names to concrete instances.

This is the only place that knows which classes exist. The pipeline asks for
"the ASR provider" and gets back whatever `ECHONEURA_ASR_PROVIDER` selects.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.providers.base import (
    ASRProvider,
    DiarizationProvider,
    EnrichmentProvider,
    ProviderUnavailable,
)
from app.providers.cloud import (
    AnthropicEnrichmentProvider,
    AssemblyAIProvider,
    DeepgramProvider,
    GroqWhisperProvider,
    OpenAIEnrichmentProvider,
)
from app.providers.local import FasterWhisperProvider, PyannoteDiarizationProvider
from app.providers.mock import MockASRProvider, MockDiarizationProvider, MockEnrichmentProvider


class NullDiarizationProvider(DiarizationProvider):
    """Explicitly no diarization: one speaker for the whole recording."""

    name = "none"

    def diarize(
        self,
        audio_path,
        *,
        duration_seconds: float | None = None,
        num_speakers: int | None = None,
    ):
        from app.providers.base import DiarizationResult, DiarizationTurn

        duration = float(duration_seconds or 0.0)
        turns = [DiarizationTurn(speaker="S0", start=0.0, end=duration)] if duration > 0 else []
        return DiarizationResult(turns=turns, provider=self.name, speaker_count=1 if turns else 0)


@lru_cache
def get_asr_provider() -> ASRProvider:
    name = settings.asr_provider
    if name == "mock":
        return MockASRProvider()
    if name == "faster_whisper":
        return FasterWhisperProvider()
    if name == "assemblyai":
        return AssemblyAIProvider()
    if name == "deepgram":
        return DeepgramProvider()
    if name == "groq_whisper":
        return GroqWhisperProvider()
    raise ProviderUnavailable(f"Unknown ECHONEURA_ASR_PROVIDER={name!r}")


@lru_cache
def get_diarization_provider() -> DiarizationProvider | None:
    name = settings.diarization_provider
    if name == "none":
        return NullDiarizationProvider()
    if name == "mock":
        return MockDiarizationProvider()
    if name == "pyannote":
        return PyannoteDiarizationProvider()
    if name in {"assemblyai", "deepgram"}:
        # These vendors diarize inside the ASR call. If the ASR provider is not
        # the same vendor, there is nothing for a standalone diarizer to do.
        if settings.asr_provider == name:
            return None
        return NullDiarizationProvider()
    raise ProviderUnavailable(f"Unknown ECHONEURA_DIARIZATION_PROVIDER={name!r}")


@lru_cache
def get_enrichment_provider() -> EnrichmentProvider:
    name = settings.enrich_provider
    if name == "mock":
        return MockEnrichmentProvider()
    if name == "openai":
        return OpenAIEnrichmentProvider()
    if name == "anthropic":
        return AnthropicEnrichmentProvider()
    raise ProviderUnavailable(f"Unknown ECHONEURA_ENRICH_PROVIDER={name!r}")


def provider_summary() -> dict[str, str]:
    asr = get_asr_provider()
    diar = get_diarization_provider()
    enrich = get_enrichment_provider()
    return {
        "asr": asr.name,
        "asr_supports_diarization": str(asr.supports_diarization).lower(),
        "diarization": diar.name if diar else "bundled-with-asr",
        "enrichment": enrich.name,
    }


__all__ = [
    "NullDiarizationProvider",
    "get_asr_provider",
    "get_diarization_provider",
    "get_enrichment_provider",
    "provider_summary",
]

"""Application configuration.

Everything is env-driven so the same code runs against the offline mock provider
on a laptop and against real GPU/API providers in production.
See `.env.example` for the full contract.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# repo_root/backend/app/core/config.py -> repo_root
REPO_ROOT = Path(__file__).resolve().parents[3]

ASRProviderName = Literal[
    "mock",
    "faster_whisper",
    "assemblyai",
    "deepgram",
    "groq_whisper",
]
DiarizationProviderName = Literal["none", "mock", "pyannote", "assemblyai", "deepgram"]
EnrichProviderName = Literal["mock", "openai", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECHONEURA_",
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "EchoNeura"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    # Comma-separated list of allowed browser origins. The sandbox preview host
    # changes per session, so we default to "*" in development and require an
    # explicit list in production.
    cors_origins: str = "*"

    # --- Storage / DB ---
    database_url: str = Field(
        default=f"sqlite:///{REPO_ROOT / 'data' / 'echoneura.db'}",
    )
    upload_dir: Path = REPO_ROOT / "data" / "uploads"
    max_upload_mb: int = 500

    # --- Providers ---
    # Defaults are "mock" so the whole app runs offline with zero keys.
    #
    # diarization_provider="none" means: use the ASR vendor's bundled
    # diarization when it has one (assemblyai/deepgram/mock), otherwise a single
    # speaker. Set it to "pyannote" for split ASR+diarization (faster-whisper),
    # which takes precedence over the vendor's bundled labels.
    asr_provider: ASRProviderName = "mock"
    diarization_provider: DiarizationProviderName = "none"
    enrich_provider: EnrichProviderName = "mock"

    # --- Provider credentials (all optional; only the active one is required) ---
    assemblyai_api_key: str | None = None
    deepgram_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    huggingface_token: str | None = None  # pyannote gating

    # --- Local model tuning ---
    whisper_model_size: str = "base"  # tiny|base|small|medium|large-v3
    whisper_device: str = "auto"  # auto|cpu|cuda
    whisper_compute_type: str = "int8"  # int8 (CPU) | float16 (GPU)
    pyannote_diarization_model: str = "pyannote/speaker-diarization-3.1"

    # --- Worker ---
    worker_poll_seconds: float = 0.5
    worker_max_concurrent_jobs: int = 1
    worker_lease_timeout_seconds: int = 900
    # Run the queue worker inside the API process (single-container dev mode).
    worker_in_process: bool = True

    # --- Pipeline defaults ---
    default_language: str = "auto"
    min_segment_words: int = 1
    max_srt_chars_per_line: int = 42
    max_srt_lines_per_cue: int = 2

    # --- Feature flags (later milestones) ---
    enable_quote_cards: bool = False  # M3
    enable_summary: bool = False  # M2
    enable_translator: bool = False  # M6

    @field_validator("upload_dir", mode="before")
    @classmethod
    def _expand_upload_dir(cls, v: object) -> object:
        if isinstance(v, str):
            return Path(v).expanduser().resolve()
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        raw = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return raw or ["*"]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def active_provider_requirements(self) -> list[str]:
        """Human-readable list of missing config for the selected providers."""
        missing: list[str] = []
        if self.asr_provider == "assemblyai" and not self.assemblyai_api_key:
            missing.append("ECHONEURA_ASSEMBLYAI_API_KEY")
        if self.asr_provider == "deepgram" and not self.deepgram_api_key:
            missing.append("ECHONEURA_DEEPGRAM_API_KEY")
        if self.asr_provider == "groq_whisper" and not self.groq_api_key:
            missing.append("ECHONEURA_GROQ_API_KEY")
        if self.diarization_provider == "pyannote" and not self.huggingface_token:
            missing.append("ECHONEURA_HUGGINGFACE_TOKEN (pyannote is a gated model)")
        if self.enrich_provider == "openai" and not self.openai_api_key:
            missing.append("ECHONEURA_OPENAI_API_KEY")
        if self.enrich_provider == "anthropic" and not self.anthropic_api_key:
            missing.append("ECHONEURA_ANTHROPIC_API_KEY")
        return missing


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()

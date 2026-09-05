"""Live-voice utterance processing (M1.5).

One utterance = one ASR call + one assistant call, synchronously. This is the
low-latency conversational path; the batch Job pipeline (upload -> queue ->
worker) stays untouched for long recordings.

Supported input formats:
    wav        any ffmpeg-decodable WAV (probed for real duration)
    pcm_s16le  raw mono signed-16-bit little-endian at `sample_rate` (what
               embedded clients like a rooted Echo Dot push most easily) —
               wrapped into a WAV header before hitting the provider layer,
               because every adapter speaks files, not byte streams.
"""

from __future__ import annotations

import io
import json
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import VoiceUtterance, utcnow
from app.providers.assistant import AssistantResponse
from app.providers.registry import get_asr_provider, get_assistant_provider

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"wav", "pcm_s16le"}


class VoiceError(Exception):
    """Client-facing utterance error (empty/too short/too long/bad format)."""

    def __init__(self, message: str, *, status_code: int = 400, fatal: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.fatal = fatal


@dataclass(slots=True)
class UtteranceOutcome:
    utterance: VoiceUtterance
    assistant: AssistantResponse


def pcm_s16le_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM in a canonical WAV header (pure stdlib, no ffmpeg needed)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # Trim any partial trailing sample so the header never lies.
        w.writeframes(pcm[: len(pcm) - (len(pcm) % 2)])
    return buffer.getvalue()


def pcm_seconds(num_bytes: int, sample_rate: int) -> float:
    return num_bytes / (2.0 * max(1, sample_rate))


def process_utterance(
    db: Session,
    audio: bytes,
    *,
    audio_format: str = "wav",
    sample_rate: int | None = None,
    language: str = "auto",
    source: str = "http",
    save_audio: bool = True,
) -> UtteranceOutcome:
    """ASR + assistant for one utterance; persists a VoiceUtterance row."""
    started = time.monotonic()

    if audio_format not in SUPPORTED_FORMATS:
        raise VoiceError(
            f"Unsupported format '{audio_format}'. Send one of: {', '.join(sorted(SUPPORTED_FORMATS))}."
        )
    sr = int(sample_rate or settings.voice_sample_rate)
    if not (8_000 <= sr <= 48_000):
        raise VoiceError(f"Sample rate {sr} Hz is outside the supported 8000-48000 Hz range.")
    if not audio:
        raise VoiceError("Empty audio payload.")

    # Duration guard BEFORE any disk/ASR work — this is also a memory-abuse cap.
    if audio_format == "pcm_s16le":
        seconds = pcm_seconds(len(audio), sr)
    else:
        # WAV header carries the length; trust it for the guard, ffmpeg verifies later.
        seconds = _wav_duration_estimate(audio, sr)
    if seconds > settings.voice_max_seconds:
        raise VoiceError(
            f"Utterance is {seconds:.0f}s, over the {settings.voice_max_seconds:.0f}s limit. "
            "For long recordings use POST /api/jobs (the batch pipeline) instead.",
            status_code=413,
            fatal=True,
        )
    if seconds < settings.voice_min_seconds:
        raise VoiceError(
            f"Utterance is {seconds:.2f}s — shorter than the {settings.voice_min_seconds}s "
            "minimum. Likely a misfire; discarded.",
            status_code=422,
        )

    wav_bytes = pcm_s16le_to_wav(audio, sr) if audio_format == "pcm_s16le" else audio

    utterance = VoiceUtterance(
        source=source,
        audio_format=audio_format,
        sample_rate=sr,
        audio_seconds=round(seconds, 3),
        language=language or "auto",
        created_at=utcnow(),
    )

    audio_path: Path | None = None
    if save_audio:
        settings.voice_dir.mkdir(parents=True, exist_ok=True)
        audio_path = settings.voice_dir / f"{utterance.id}.wav"
        audio_path.write_bytes(wav_bytes)
        utterance.audio_path = str(audio_path)

    # --- ASR (same provider stack as the batch pipeline) ---
    asr = get_asr_provider()
    asr.check_available()
    tmp_path = audio_path
    if tmp_path is None:
        tmp_path = settings.voice_dir / f"{utterance.id}.tmp.wav"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(wav_bytes)
    try:
        result = asr.transcribe(
            tmp_path,
            language=language or "auto",
            duration_seconds=seconds,
            want_diarization=False,  # single-speaker command audio
            want_words=False,  # latency over granularity for voice
        )
    finally:
        if audio_path is None:
            tmp_path.unlink(missing_ok=True)

    transcript = result.text.strip()
    utterance.transcript = transcript
    utterance.detected_language = result.language
    utterance.asr_provider = result.provider
    confidences = [s.confidence for s in result.segments if s.confidence is not None]
    utterance.confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
    if result.duration_seconds:
        utterance.audio_seconds = round(result.duration_seconds, 3)

    # --- Assistant bridge (the "next actions" hook) ---
    assistant = get_assistant_provider()
    assistant.check_available()
    response = assistant.act(transcript, language=result.language, context={"source": source})
    utterance.assistant_provider = response.provider
    utterance.assistant_reply = response.reply
    utterance.assistant_action = response.action
    utterance.assistant_data_json = json.dumps(response.data, ensure_ascii=False)

    utterance.processing_ms = int((time.monotonic() - started) * 1000)
    db.add(utterance)
    db.commit()
    logger.info(
        "Utterance %s: %.1fs audio -> %r -> %s (%dms)",
        utterance.id,
        utterance.audio_seconds,
        transcript[:60],
        response.action,
        utterance.processing_ms,
    )
    return UtteranceOutcome(utterance=utterance, assistant=response)


def _wav_duration_estimate(data: bytes, fallback_rate: int) -> float:
    """Read duration from a WAV header without writing to disk; fall back to PCM math."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate() or fallback_rate
            return w.getnframes() / float(rate)
    except (wave.Error, EOFError):
        return len(data) / (2.0 * max(1, fallback_rate))


def utterance_to_dict(u: VoiceUtterance) -> dict:
    """Canonical wire shape shared by HTTP responses and WebSocket messages."""
    try:
        data = json.loads(u.assistant_data_json) if u.assistant_data_json else {}
    except json.JSONDecodeError:
        data = {}
    return {
        "id": u.id,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "source": u.source,
        "audio_seconds": u.audio_seconds,
        "language": u.language,
        "detected_language": u.detected_language,
        "asr_provider": u.asr_provider,
        "transcript": u.transcript,
        "confidence": u.confidence,
        "assistant": {
            "provider": u.assistant_provider,
            "reply": u.assistant_reply,
            "action": u.assistant_action,
            "data": data,
        },
        "processing_ms": u.processing_ms,
    }

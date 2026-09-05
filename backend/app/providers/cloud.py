"""Cloud provider adapters (AssemblyAI, Deepgram, Groq, OpenAI, Anthropic).

Every adapter is pure `httpx` — no vendor SDKs — so the dependency surface stays
tiny and the request/response mapping is visible and unit-testable.

Each one is exercised in tests with a mocked transport, which means the payload
parsing is verified without spending money or leaking keys in CI.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx

from app.core.config import settings
from app.providers.base import (
    ASRProvider,
    ASRResult,
    ASRSegment,
    ASRWord,
    EnrichmentProvider,
    ProviderError,
    ProviderUnavailable,
)

logger = logging.getLogger(__name__)

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    backoff: float = 1.5,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            resp = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            logger.warning("Transport error calling %s (%s); retry %d", url, exc, i + 1)
            time.sleep(backoff**i)
            continue
        if resp.status_code in RETRY_STATUS and i < attempts - 1:
            retry_after = resp.headers.get("retry-after")
            delay = (
                float(retry_after)
                if (retry_after or "").replace(".", "", 1).isdigit()
                else backoff**i
            )
            logger.warning("HTTP %s from %s; retrying in %.1fs", resp.status_code, url, delay)
            time.sleep(min(delay, 30.0))
            continue
        return resp
    if last_exc:
        raise ProviderError(f"Network failure calling {url}: {last_exc}") from last_exc
    raise ProviderUnavailable(f"Request to {url} failed after {attempts} attempts")


# --------------------------------------------------------------------------- #
# AssemblyAI — ASR *and* diarization in one call
# --------------------------------------------------------------------------- #
class AssemblyAIProvider(ASRProvider):
    """https://www.assemblyai.com/docs

    Flow: POST /v2/upload (bytes) -> upload_url
          POST /v2/transcript     -> id
          GET  /v2/transcript/{id} until completed
    """

    name = "assemblyai"
    supports_diarization = True
    supports_word_timestamps = True

    BASE = "https://api.assemblyai.com"

    def __init__(self, api_key: str | None = None, poll_timeout: float = 1800.0) -> None:
        self.api_key = api_key or settings.assemblyai_api_key
        self.poll_timeout = poll_timeout

    def check_available(self) -> None:
        if not self.api_key:
            raise ProviderUnavailable(
                "ECHONEURA_ASSEMBLYAI_API_KEY is not set. Get a key at assemblyai.com "
                "or use ECHONEURA_ASR_PROVIDER=mock for offline development."
            )

    def _headers(self) -> dict[str, str]:
        return {"authorization": self.api_key}

    def _upload(self, client: httpx.Client, audio_path: Path) -> str:
        with audio_path.open("rb") as fh:
            resp = _request_with_retry(
                client,
                "POST",
                f"{self.BASE}/v2/upload",
                headers={**self._headers(), "content-type": "application/octet-stream"},
                content=fh.read(),
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
        if resp.status_code >= 400:
            raise ProviderError(f"AssemblyAI upload failed ({resp.status_code}): {resp.text[:400]}")
        return resp.json()["upload_url"]

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "auto",
        duration_seconds: float | None = None,
        want_diarization: bool = False,
        want_words: bool = True,
    ) -> ASRResult:
        self.check_available()
        payload: dict[str, Any] = {
            "speech_model": "best",
            "punctuate": True,
            "format_text": True,
            "word_boost": [],
        }
        if language and language != "auto":
            payload["language_code"] = language
        else:
            payload["language_detection"] = True
        if want_diarization:
            payload["diarize"] = True
        if want_words:
            payload["word_timestamps"] = True

        with httpx.Client(headers=self._headers()) as client:
            payload["audio_url"] = self._upload(client, audio_path)
            created = _request_with_retry(
                client, "POST", f"{self.BASE}/v2/transcript", json=payload, timeout=120.0
            )
            if created.status_code >= 400:
                raise ProviderError(
                    f"AssemblyAI transcript request failed ({created.status_code}): {created.text[:400]}"
                )
            transcript_id = created.json()["id"]

            deadline = time.monotonic() + self.poll_timeout
            while True:
                poll = _request_with_retry(
                    client, "GET", f"{self.BASE}/v2/transcript/{transcript_id}", timeout=60.0
                )
                data = poll.json()
                status = data.get("status")
                if status == "completed":
                    break
                if status == "error":
                    raise ProviderError(f"AssemblyAI job errored: {data.get('error')}")
                if time.monotonic() > deadline:
                    raise ProviderError(
                        f"AssemblyAI transcript {transcript_id} did not finish within "
                        f"{int(self.poll_timeout)}s (last status: {status})."
                    )
                time.sleep(min(5.0, max(1.0, self.poll_timeout / 300)))

        return self._parse(data)

    @staticmethod
    def _parse(data: dict[str, Any]) -> ASRResult:
        """Map an AssemblyAI transcript payload into our canonical shape.

        Prefers `utterances` (already speaker-segmented) and falls back to
        `paragraphs`/`words` when diarization was off.
        """
        segments: list[ASRSegment] = []
        utterances = data.get("utterances") or []
        for u in utterances:
            words = [
                ASRWord(
                    text=str(w.get("text", "")).strip(),
                    start=float(w.get("start", 0)) / 1000.0,
                    end=float(w.get("end", 0)) / 1000.0,
                    confidence=float(w["confidence"]) if w.get("confidence") is not None else None,
                )
                for w in (u.get("words") or [])
                if str(w.get("text", "")).strip()
            ]
            segments.append(
                ASRSegment(
                    text=str(u.get("text", "")).strip(),
                    start=float(u.get("start", 0)) / 1000.0,
                    end=float(u.get("end", 0)) / 1000.0,
                    speaker=u.get("speaker"),
                    confidence=float(u["confidence"]) if u.get("confidence") is not None else None,
                    words=words,
                )
            )

        if not segments:
            paragraphs = (data.get("paragraphs") or {}).get("paragraphs") or []
            for p in paragraphs:
                words = [
                    ASRWord(
                        text=str(w.get("text", "")).strip(),
                        start=float(w.get("start", 0)) / 1000.0,
                        end=float(w.get("end", 0)) / 1000.0,
                        confidence=float(w["confidence"])
                        if w.get("confidence") is not None
                        else None,
                    )
                    for w in (p.get("words") or [])
                ]
                segments.append(
                    ASRSegment(
                        text=str(p.get("text", "")).strip(),
                        start=float(p.get("start", 0)) / 1000.0,
                        end=float(p.get("end", 0)) / 1000.0,
                        speaker=p.get("speaker"),
                        confidence=None,
                        words=words,
                    )
                )

        if not segments and data.get("words"):
            # No paragraph/utterance grouping: build one segment per ~20 words.
            chunk: list[ASRWord] = []
            for w in data["words"]:
                chunk.append(
                    ASRWord(
                        text=str(w.get("text", "")).strip(),
                        start=float(w.get("start", 0)) / 1000.0,
                        end=float(w.get("end", 0)) / 1000.0,
                        confidence=float(w["confidence"])
                        if w.get("confidence") is not None
                        else None,
                    )
                )
                if len(chunk) >= 20 or chunk[-1].text.endswith((".", "?", "!")):
                    segments.append(
                        ASRSegment(
                            text=" ".join(c.text for c in chunk),
                            start=chunk[0].start,
                            end=chunk[-1].end,
                            speaker=None,
                            words=chunk,
                        )
                    )
                    chunk = []
            if chunk:
                segments.append(
                    ASRSegment(
                        text=" ".join(c.text for c in chunk),
                        start=chunk[0].start,
                        end=chunk[-1].end,
                        speaker=None,
                        words=chunk,
                    )
                )

        lang = data.get("language_code") or data.get("language_detection", {}).get("language_code")
        return ASRResult(
            segments=[s for s in segments if s.text],
            provider="assemblyai",
            language=lang,
            language_confidence=None,
            duration_seconds=float(data["audio_duration"]) if data.get("audio_duration") else None,
            raw={"id": data.get("id"), "status": data.get("status")},
        )


# --------------------------------------------------------------------------- #
# Deepgram — synchronous, ASR + diarization in one call
# --------------------------------------------------------------------------- #
class DeepgramProvider(ASRProvider):
    """https://developers.deepgram.com/reference/pre-recorded-transcription

    Synchronous: POST the audio bytes, get JSON back. No polling, which makes it
    the cheapest-latency option for files under a few hundred MB.
    """

    name = "deepgram"
    supports_diarization = True
    supports_word_timestamps = True

    BASE = "https://api.deepgram.com/v1/listen"
    # Deepgram accepts most containers directly; we pass the original bytes so
    # video files (mp4/mov) work without a separate audio extraction step.
    MIME_BY_EXT: ClassVar[dict[str, str]] = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".webm": "audio/webm",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }

    def __init__(self, api_key: str | None = None, model: str = "nova-3") -> None:
        self.api_key = api_key or settings.deepgram_api_key
        self.model = model

    def check_available(self) -> None:
        if not self.api_key:
            raise ProviderUnavailable(
                "ECHONEURA_DEEPGRAM_API_KEY is not set. Get a key at deepgram.com "
                "or use ECHONEURA_ASR_PROVIDER=mock for offline development."
            )

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "auto",
        duration_seconds: float | None = None,
        want_diarization: bool = False,
        want_words: bool = True,
    ) -> ASRResult:
        self.check_available()
        params: dict[str, Any] = {
            "model": self.model,
            "punctuate": "true",
            "smart_format": "true",
            "utterances": "true",  # gives us speaker-grouped lines
        }
        if want_diarization:
            params["diarize"] = "true"
        if language and language != "auto":
            params["language"] = language
        else:
            params["detect_language"] = "true"

        mime = self.MIME_BY_EXT.get(audio_path.suffix.lower(), "application/octet-stream")
        with httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0)) as client:
            resp = _request_with_retry(
                client,
                "POST",
                self.BASE,
                params=params,
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": mime,
                },
                content=audio_path.read_bytes(),
            )
        if resp.status_code >= 400:
            raise ProviderError(f"Deepgram request failed ({resp.status_code}): {resp.text[:400]}")
        return self._parse(resp.json())

    @staticmethod
    def _parse(data: dict[str, Any]) -> ASRResult:
        result = data.get("results") or {}
        channels = result.get("channels") or []
        alternatives = (channels[0].get("alternatives") or [{}]) if channels else [{}]
        alt = alternatives[0] if alternatives else {}

        words = [
            ASRWord(
                text=str(w.get("word", "")).strip(),
                start=float(w.get("start", 0.0)),
                end=float(w.get("end", 0.0)),
                confidence=float(w["confidence"]) if w.get("confidence") is not None else None,
            )
            for w in (alt.get("words") or [])
            if str(w.get("word", "")).strip()
        ]

        segments: list[ASRSegment] = []
        utterances = result.get("utterances") or []
        for u in utterances:
            start, end = float(u.get("start", 0.0)), float(u.get("end", 0.0))
            # Attach the word timings that fall inside this utterance.
            seg_words = [w for w in words if w.start >= start - 0.05 and w.end <= end + 0.05]
            speaker = u.get("speaker")
            segments.append(
                ASRSegment(
                    text=str(u.get("transcript", "")).strip(),
                    start=start,
                    end=end,
                    speaker=str(speaker) if speaker is not None else None,
                    confidence=float(u["confidence"]) if u.get("confidence") is not None else None,
                    words=seg_words,
                )
            )

        if not segments and words:
            # No utterances (diarize off): group words into readable lines.
            chunk: list[ASRWord] = []
            for w in words:
                chunk.append(w)
                if len(chunk) >= 20 or w.text.endswith((".", "?", "!")):
                    segments.append(
                        ASRSegment(
                            text=" ".join(c.text for c in chunk),
                            start=chunk[0].start,
                            end=chunk[-1].end,
                            speaker=None,
                            words=chunk,
                        )
                    )
                    chunk = []
            if chunk:
                segments.append(
                    ASRSegment(
                        text=" ".join(c.text for c in chunk),
                        start=chunk[0].start,
                        end=chunk[-1].end,
                        speaker=None,
                        words=chunk,
                    )
                )

        detected = result.get("channels", [{}])[0].get("detected_language") if channels else None
        duration = None
        meta = data.get("metadata") or {}
        if meta.get("duration"):
            duration = float(meta["duration"])
        return ASRResult(
            segments=[s for s in segments if s.text],
            provider="deepgram",
            language=detected or None,
            language_confidence=None,
            duration_seconds=duration,
            raw={
                "request_id": meta.get("request_id"),
                "model": (data.get("model") or {}).get("name"),
            },
        )


# --------------------------------------------------------------------------- #
# Groq Whisper — very fast, very cheap, no diarization
# --------------------------------------------------------------------------- #
class GroqWhisperProvider(ASRProvider):
    """https://console.groq.com/docs/speech-to-text

    Fastest $/hour option for ASR. It does NOT diarize, so pair it with
    ECHONEURA_DIARIZATION_PROVIDER=pyannote (or accept a single speaker).
    """

    name = "groq_whisper"
    supports_diarization = False
    supports_word_timestamps = True

    BASE = "https://api.groq.com/openai/v1/audio/transcriptions"
    MODEL = "whisper-large-v3"
    MAX_BYTES = 100 * 1024 * 1024  # Groq request size cap

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.groq_api_key
        self.model = model or self.MODEL

    def check_available(self) -> None:
        if not self.api_key:
            raise ProviderUnavailable(
                "ECHONEURA_GROQ_API_KEY is not set. Create one at console.groq.com "
                "or use ECHONEURA_ASR_PROVIDER=mock for offline development."
            )

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "auto",
        duration_seconds: float | None = None,
        want_diarization: bool = False,
        want_words: bool = True,
    ) -> ASRResult:
        self.check_available()
        size = audio_path.stat().st_size
        if size > self.MAX_BYTES:
            raise ProviderUnavailable(
                f"{audio_path.name} is {size / 1e6:.1f} MB, over Groq's "
                f"{self.MAX_BYTES / 1e6:.0f} MB limit. Shorten/split the file, or use "
                "ECHONEURA_ASR_PROVIDER=assemblyai|deepgram which accept larger uploads."
            )

        data: dict[str, Any] = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment" + (",word" if want_words else ""),
        }
        if language and language != "auto":
            data["language"] = language

        with (
            httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0)) as client,
            audio_path.open("rb") as fh,
        ):
            resp = _request_with_retry(
                client,
                "POST",
                self.BASE,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files={"file": (audio_path.name, fh, "application/octet-stream")},
            )
        if resp.status_code >= 400:
            raise ProviderError(f"Groq request failed ({resp.status_code}): {resp.text[:400]}")
        payload = resp.json()

        segments: list[ASRSegment] = []
        for s in payload.get("segments") or []:
            segments.append(
                ASRSegment(
                    text=str(s.get("text", "")).strip(),
                    start=float(s.get("start", 0.0)),
                    end=float(s.get("end", 0.0)),
                    speaker=None,
                    confidence=(
                        float(s["avg_logprob"]) if s.get("avg_logprob") is not None else None
                    ),
                    words=[],
                )
            )

        words = [
            ASRWord(
                text=str(w.get("word", "")).strip(),
                start=float(w.get("start", 0.0)),
                end=float(w.get("end", 0.0)),
            )
            for w in payload.get("words") or []
            if str(w.get("word", "")).strip()
        ]
        # Groq returns words at the top level; distribute them into segments.
        if words and segments:
            for seg in segments:
                seg.words = [
                    w for w in words if w.start >= seg.start - 0.05 and w.end <= seg.end + 0.05
                ]
        elif words and not segments:
            segments.append(
                ASRSegment(
                    text=str(payload.get("text", "")).strip(),
                    start=words[0].start,
                    end=words[-1].end,
                    speaker=None,
                    words=words,
                )
            )
        elif not segments and payload.get("text"):
            segments.append(
                ASRSegment(
                    text=str(payload["text"]).strip(),
                    start=0.0,
                    end=float(payload.get("duration") or 0.0),
                    speaker=None,
                    words=[],
                )
            )

        return ASRResult(
            segments=[s for s in segments if s.text],
            provider="groq_whisper",
            language=payload.get("language"),
            duration_seconds=float(payload["duration"])
            if payload.get("duration")
            else duration_seconds,
            raw={"model": payload.get("model")},
        )


# --------------------------------------------------------------------------- #
# Enrichment (M2/M3) — summary, key points, pull quotes
# --------------------------------------------------------------------------- #
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_ENRICH_SYSTEM = (
    "You are a precise audio-content editor. You never invent facts, names or quotes. "
    "You always answer with a single valid JSON object and no prose outside it."
)

_ENRICH_INSTRUCTIONS = """\
Given the transcript below, return JSON with exactly these keys:
  "summary":   3-5 sentence neutral summary in the transcript's own language.
  "key_points": array of 3-6 short bullet strings.
  "quotes":     array of up to {limit} objects {{"text": <verbatim sentence(s) from the transcript>,
               "speaker": <speaker label if known, else null>,
               "reason": <why it is share-worthy, <= 15 words>}}.
Rules for "quotes": copy the text VERBATIM from the transcript. Never paraphrase.
Prefer concrete, self-contained, non-offensive lines.

TRANSCRIPT:
{transcript}
"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object from the enrichment model")
    return data


class OpenAIEnrichmentProvider(EnrichmentProvider):
    name = "openai"
    BASE = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key or settings.openai_api_key
        self.model = model

    def check_available(self) -> None:
        if not self.api_key:
            raise ProviderUnavailable(
                "ECHONEURA_OPENAI_API_KEY is not set (needed for M2 summaries/quotes)."
            )

    def _complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.check_available()
        with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            resp = _request_with_retry(
                client,
                "POST",
                self.BASE,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": _ENRICH_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
        if resp.status_code >= 400:
            raise ProviderError(f"OpenAI request failed ({resp.status_code}): {resp.text[:400]}")
        return resp.json()["choices"][0]["message"]["content"]

    def _enrich_json(self, transcript_text: str, limit: int) -> dict[str, Any]:
        prompt = _ENRICH_INSTRUCTIONS.format(limit=limit, transcript=transcript_text[:60_000])
        return _extract_json(self._complete(prompt))

    def summarize(self, transcript_text: str, *, language: str | None = None) -> str:
        data = self._enrich_json(transcript_text, 3)
        return str(data.get("summary", "")).strip()

    def key_points(
        self, transcript_text: str, *, language: str | None = None, limit: int = 5
    ) -> list[str]:
        data = self._enrich_json(transcript_text, limit)
        return [str(k).strip() for k in (data.get("key_points") or []) if str(k).strip()][:limit]

    def quotes(
        self, transcript_text: str, *, language: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        data = self._enrich_json(transcript_text, limit)
        return [q for q in (data.get("quotes") or []) if isinstance(q, dict)][:limit]


class AnthropicEnrichmentProvider(EnrichmentProvider):
    name = "anthropic"
    BASE = "https://api.anthropic.com/v1/messages"
    VERSION = "2023-06-01"

    def __init__(self, api_key: str | None = None, model: str = "claude-3-5-haiku-latest") -> None:
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model

    def check_available(self) -> None:
        if not self.api_key:
            raise ProviderUnavailable(
                "ECHONEURA_ANTHROPIC_API_KEY is not set (needed for M2 summaries/quotes)."
            )

    def _complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.check_available()
        with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            resp = _request_with_retry(
                client,
                "POST",
                self.BASE,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 2048,
                    "temperature": temperature,
                    "system": _ENRICH_SYSTEM,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        if resp.status_code >= 400:
            raise ProviderError(f"Anthropic request failed ({resp.status_code}): {resp.text[:400]}")
        blocks = resp.json().get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def _enrich_json(self, transcript_text: str, limit: int) -> dict[str, Any]:
        prompt = _ENRICH_INSTRUCTIONS.format(limit=limit, transcript=transcript_text[:60_000])
        return _extract_json(self._complete(prompt))

    def summarize(self, transcript_text: str, *, language: str | None = None) -> str:
        return str(self._enrich_json(transcript_text, 3).get("summary", "")).strip()

    def key_points(
        self, transcript_text: str, *, language: str | None = None, limit: int = 5
    ) -> list[str]:
        data = self._enrich_json(transcript_text, limit)
        return [str(k).strip() for k in (data.get("key_points") or []) if str(k).strip()][:limit]

    def quotes(
        self, transcript_text: str, *, language: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        data = self._enrich_json(transcript_text, limit)
        return [q for q in (data.get("quotes") or []) if isinstance(q, dict)][:limit]


def encode_data_uri(path: Path) -> str:
    """Helper for providers that need base64 audio inline (kept for future use)."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:audio/{path.suffix.lstrip('.') or 'mpeg'};base64,{b64}"

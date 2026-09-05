"""Cloud provider adapters, tested against mocked HTTP transports.

Real vendor payloads (shape-accurate) go in, canonical `ASRResult`/enrichment
objects come out. No network, no keys, no spend — but the parsing bugs that would
otherwise only show up on a production invoice get caught here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.providers.base import ProviderError, ProviderUnavailable
from app.providers.cloud import (
    AnthropicEnrichmentProvider,
    AssemblyAIProvider,
    DeepgramProvider,
    GroqWhisperProvider,
    OpenAIEnrichmentProvider,
    _extract_json,
)

Handler = Callable[[httpx.Request], httpx.Response]


def resp(status_code: int, payload: Any) -> httpx.Response:
    request = httpx.Request("POST", "https://example.invalid")
    if isinstance(payload, (dict, list)):
        return httpx.Response(status_code, json=payload, request=request)
    return httpx.Response(status_code, text=str(payload), request=request)


PASSTHROUGH_KWARGS = ("params", "json", "headers", "content", "data", "files")


def install(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    """Route every httpx.Client request in the adapters through `handler`.

    Only body-ish kwargs are forwarded, so timeouts and other transport options
    are dropped rather than tripping the httpx.Request signature.
    """

    def fake_request(self, method, url, **kwargs):
        forwarded = {k: v for k, v in kwargs.items() if k in PASSTHROUGH_KWARGS}
        request = httpx.Request(method, url, **forwarded)
        request.read()  # materialise multipart/file bodies so tests can inspect them
        return handler(request)

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr("app.providers.cloud.time.sleep", lambda *_: None)


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "interview.mp3"
    path.write_bytes(b"ID3fake-audio-bytes" * 100)
    return path


# --------------------------------------------------------------------------- #
# AssemblyAI
# --------------------------------------------------------------------------- #
ASSEMBLYAI_COMPLETED = {
    "id": "tr-123",
    "status": "completed",
    "audio_duration": 42.5,
    "language_code": "en",
    "text": "Hello there. Nice to meet you.",
    "utterances": [
        {
            "text": "Hello there.",
            "start": 320,
            "end": 1850,
            "confidence": 0.95,
            "speaker": "A",
            "words": [
                {"text": "Hello", "start": 320, "end": 900, "confidence": 0.97},
                {"text": "there.", "start": 900, "end": 1850, "confidence": 0.93},
            ],
        },
        {
            "text": "Nice to meet you.",
            "start": 2100,
            "end": 3600,
            "confidence": 0.91,
            "speaker": "B",
            "words": [
                {"text": "Nice", "start": 2100, "end": 2400, "confidence": 0.9},
                {"text": "to", "start": 2400, "end": 2500, "confidence": 0.99},
                {"text": "meet", "start": 2500, "end": 3000, "confidence": 0.92},
                {"text": "you.", "start": 3000, "end": 3600, "confidence": 0.88},
            ],
        },
    ],
}


def test_assemblyai_requires_a_key(audio: Path) -> None:
    provider = AssemblyAIProvider(api_key=None)
    with pytest.raises(ProviderUnavailable, match="ASSEMBLYAI_API_KEY"):
        provider.check_available()
    with pytest.raises(ProviderUnavailable):
        provider.transcribe(audio)


def test_assemblyai_parses_utterances(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v2/upload":
            return resp(200, {"upload_url": "https://cdn.assemblyai.com/upload/abc"})
        if request.method == "POST" and request.url.path == "/v2/transcript":
            return resp(200, {"id": "tr-123", "status": "queued"})
        return resp(200, ASSEMBLYAI_COMPLETED)

    install(monkeypatch, handler)
    provider = AssemblyAIProvider(api_key="test-key")
    result = provider.transcribe(audio, language="auto", want_diarization=True)

    assert seen[0] == "POST /v2/upload"
    assert seen[1] == "POST /v2/transcript"
    assert any(p.startswith("GET /v2/transcript/") for p in seen), "must poll for completion"

    assert result.provider == "assemblyai"
    assert result.language == "en"
    assert result.duration_seconds == 42.5
    assert [s.speaker for s in result.segments] == ["A", "B"]
    # Milliseconds in, seconds out — the single most common vendor-unit bug.
    assert result.segments[0].start == pytest.approx(0.32)
    assert result.segments[0].end == pytest.approx(1.85)
    assert result.segments[1].text == "Nice to meet you."
    assert len(result.segments[1].words) == 4
    assert result.segments[1].words[0].start == pytest.approx(2.1)
    assert result.has_speaker_labels


def test_assemblyai_sends_expected_options(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v2/transcript":
            captured.update(json.loads(request.content))
            return resp(200, {"id": "tr-1", "status": "queued"})
        if request.url.path == "/v2/upload":
            return resp(200, {"upload_url": "https://cdn/x"})
        return resp(200, ASSEMBLYAI_COMPLETED)

    install(monkeypatch, handler)
    AssemblyAIProvider(api_key="k").transcribe(audio, language="es", want_diarization=True)

    assert captured["language_code"] == "es"
    assert captured["diarize"] is True
    assert captured["word_timestamps"] is True
    assert captured["punctuate"] is True
    assert captured["audio_url"] == "https://cdn/x"
    assert "language_detection" not in captured


def test_assemblyai_language_detection_when_auto(
    monkeypatch: pytest.MonkeyPatch, audio: Path
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v2/transcript":
            captured.update(json.loads(request.content))
            return resp(200, {"id": "tr-1", "status": "queued"})
        if request.url.path == "/v2/upload":
            return resp(200, {"upload_url": "https://cdn/x"})
        return resp(200, ASSEMBLYAI_COMPLETED)

    install(monkeypatch, handler)
    AssemblyAIProvider(api_key="k").transcribe(audio, language="auto")
    assert captured["language_detection"] is True
    assert "language_code" not in captured


def test_assemblyai_falls_back_to_paragraphs(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    payload = {
        "id": "tr-2",
        "status": "completed",
        "language_code": "fr",
        "paragraphs": {
            "paragraphs": [
                {
                    "text": "Bonjour tout le monde.",
                    "start": 1000,
                    "end": 3000,
                    "speaker": "A",
                    "words": [{"text": "Bonjour", "start": 1000, "end": 1600, "confidence": 0.9}],
                }
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/upload":
            return resp(200, {"upload_url": "https://cdn/x"})
        if request.method == "POST":
            return resp(200, {"id": "tr-2", "status": "queued"})
        return resp(200, payload)

    install(monkeypatch, handler)
    result = AssemblyAIProvider(api_key="k").transcribe(audio)
    assert len(result.segments) == 1
    assert result.segments[0].speaker == "A"
    assert result.segments[0].start == pytest.approx(1.0)
    assert result.language == "fr"


def test_assemblyai_error_status_raises(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/upload":
            return resp(200, {"upload_url": "https://cdn/x"})
        if request.method == "POST":
            return resp(200, {"id": "tr-3", "status": "queued"})
        return resp(200, {"id": "tr-3", "status": "error", "error": "Download error"})

    install(monkeypatch, handler)
    with pytest.raises(ProviderError, match="Download error"):
        AssemblyAIProvider(api_key="k").transcribe(audio)


def test_assemblyai_upload_failure_raises(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    install(monkeypatch, lambda request: resp(401, {"error": "Unauthorized"}))
    with pytest.raises(ProviderError, match="401"):
        AssemblyAIProvider(api_key="bad").transcribe(audio)


# --------------------------------------------------------------------------- #
# Deepgram
# --------------------------------------------------------------------------- #
DEEPGRAM_PAYLOAD = {
    "metadata": {"request_id": "req-1", "duration": 12.34, "channels": 1},
    "model": {"name": "nova-3"},
    "results": {
        "channels": [
            {
                "alternatives": [
                    {
                        "transcript": "Hello there. Hi Alice.",
                        "confidence": 0.94,
                        "words": [
                            {"word": "Hello", "start": 0.1, "end": 0.5, "confidence": 0.97},
                            {"word": "there.", "start": 0.5, "end": 1.0, "confidence": 0.95},
                            {"word": "Hi", "start": 1.4, "end": 1.6, "confidence": 0.9},
                            {"word": "Alice.", "start": 1.6, "end": 2.2, "confidence": 0.88},
                        ],
                    }
                ],
                "detected_language": "en",
            }
        ],
        "utterances": [
            {
                "start": 0.1,
                "end": 1.0,
                "speaker": 0,
                "transcript": "Hello there.",
                "confidence": 0.96,
            },
            {"start": 1.4, "end": 2.2, "speaker": 1, "transcript": "Hi Alice.", "confidence": 0.89},
        ],
    },
}


def test_deepgram_requires_a_key(audio: Path) -> None:
    with pytest.raises(ProviderUnavailable, match="DEEPGRAM_API_KEY"):
        DeepgramProvider(api_key=None).check_available()


def test_deepgram_parses_utterances_and_words(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        captured["auth"] = request.headers.get("Authorization")
        return resp(200, DEEPGRAM_PAYLOAD)

    install(monkeypatch, handler)
    result = DeepgramProvider(api_key="dg-key").transcribe(
        audio, language="auto", want_diarization=True
    )

    assert captured["auth"] == "Token dg-key"
    assert captured["params"]["diarize"] == "true"
    assert captured["params"]["utterances"] == "true"
    assert captured["params"]["detect_language"] == "true"
    assert captured["params"]["model"] == "nova-3"

    assert result.provider == "deepgram"
    # Deepgram already reports seconds — no unit conversion should happen.
    assert result.segments[0].start == pytest.approx(0.1)
    assert result.segments[0].end == pytest.approx(1.0)
    assert [s.speaker for s in result.segments] == ["0", "1"]
    assert result.segments[0].words[0].text == "Hello"
    assert len(result.segments[1].words) == 2
    assert result.duration_seconds == pytest.approx(12.34)


def test_deepgram_without_utterances_groups_words(
    monkeypatch: pytest.MonkeyPatch, audio: Path
) -> None:
    payload = json.loads(json.dumps(DEEPGRAM_PAYLOAD))
    del payload["results"]["utterances"]
    install(monkeypatch, lambda request: resp(200, payload))
    result = DeepgramProvider(api_key="k").transcribe(audio)
    assert result.segments, "must still produce segments from raw words"
    assert all(s.text for s in result.segments)
    assert not result.has_speaker_labels


def test_deepgram_explicit_language_param(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return resp(200, DEEPGRAM_PAYLOAD)

    install(monkeypatch, handler)
    DeepgramProvider(api_key="k").transcribe(audio, language="de")
    assert captured["params"]["language"] == "de"
    assert "detect_language" not in captured["params"]


def test_deepgram_http_error_raises(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    install(monkeypatch, lambda request: resp(400, {"err_code": "INVALID_REQUEST"}))
    with pytest.raises(ProviderError, match="400"):
        DeepgramProvider(api_key="k").transcribe(audio)


def test_deepgram_retries_on_429(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return resp(429, "rate limited") if calls["n"] < 3 else resp(200, DEEPGRAM_PAYLOAD)

    install(monkeypatch, handler)
    result = DeepgramProvider(api_key="k").transcribe(audio)
    assert calls["n"] == 3
    assert result.segments


# --------------------------------------------------------------------------- #
# Groq Whisper
# --------------------------------------------------------------------------- #
GROQ_PAYLOAD = {
    "task": "transcribe",
    "language": "en",
    "duration": 8.0,
    "text": "Hello there. Hi Alice.",
    "segments": [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "Hello there.", "avg_logprob": -0.21},
        {"id": 1, "start": 1.4, "end": 2.2, "text": "Hi Alice.", "avg_logprob": -0.35},
    ],
    "words": [
        {"word": "Hello", "start": 0.0, "end": 0.4},
        {"word": "there.", "start": 0.4, "end": 1.0},
        {"word": "Hi", "start": 1.4, "end": 1.7},
        {"word": "Alice.", "start": 1.7, "end": 2.2},
    ],
}


def test_groq_requires_a_key(audio: Path) -> None:
    with pytest.raises(ProviderUnavailable, match="GROQ_API_KEY"):
        GroqWhisperProvider(api_key=None).check_available()


def test_groq_parses_segments_and_distributes_words(
    monkeypatch: pytest.MonkeyPatch, audio: Path
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["data"] = request.content  # multipart body
        captured["auth"] = request.headers.get("Authorization")
        return resp(200, GROQ_PAYLOAD)

    install(monkeypatch, handler)
    result = GroqWhisperProvider(api_key="gsk_test").transcribe(audio, language="en")

    assert captured["auth"] == "Bearer gsk_test"
    assert b"whisper-large-v3" in captured["data"]
    assert b"verbose_json" in captured["data"]
    assert b"word" in captured["data"]  # timestamp_granularities includes word

    assert result.provider == "groq_whisper"
    assert result.language == "en"
    assert result.duration_seconds == pytest.approx(8.0)
    assert [s.text for s in result.segments] == ["Hello there.", "Hi Alice."]
    assert len(result.segments[0].words) == 2
    assert len(result.segments[1].words) == 2
    assert result.segments[0].confidence == pytest.approx(-0.21)
    assert not result.has_speaker_labels, "Groq does not diarize"


def test_groq_rejects_oversized_files(tmp_path: Path) -> None:
    provider = GroqWhisperProvider(api_key="k")
    # Sparse file: reports a size over the limit without allocating 100 MB.
    big = tmp_path / "huge.wav"
    with big.open("wb") as fh:
        fh.truncate(provider.MAX_BYTES + 1)
    assert big.stat().st_size == provider.MAX_BYTES + 1
    with pytest.raises(ProviderUnavailable, match="over Groq's"):
        provider.transcribe(big)


def test_groq_text_only_fallback(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    install(
        monkeypatch,
        lambda request: resp(200, {"text": "Just plain text.", "duration": 3.0, "language": "en"}),
    )
    result = GroqWhisperProvider(api_key="k").transcribe(audio)
    assert len(result.segments) == 1
    assert result.segments[0].text == "Just plain text."


def test_groq_http_error_raises(monkeypatch: pytest.MonkeyPatch, audio: Path) -> None:
    install(monkeypatch, lambda request: resp(401, {"error": {"message": "invalid key"}}))
    with pytest.raises(ProviderError, match="401"):
        GroqWhisperProvider(api_key="k").transcribe(audio)


# --------------------------------------------------------------------------- #
# Enrichment JSON handling
# --------------------------------------------------------------------------- #
def test_extract_json_handles_fences_and_prose() -> None:
    assert _extract_json('{"summary": "ok"}')["summary"] == "ok"
    assert _extract_json('```json\n{"summary": "ok"}\n```')["summary"] == "ok"
    assert _extract_json('Here you go:\n{"summary": "ok"}\nHope that helps.')["summary"] == "ok"
    with pytest.raises(json.JSONDecodeError):
        _extract_json("not json at all")


OPENAI_REPLY = {
    "choices": [
        {
            "message": {
                "content": json.dumps(
                    {
                        "summary": "Two speakers discuss transcript quality.",
                        "key_points": ["Diarization matters", "Editing is expected"],
                        "quotes": [
                            {
                                "text": "Diarization matters most.",
                                "speaker": "A",
                                "reason": "punchy",
                            }
                        ],
                    }
                )
            }
        }
    ]
}


def test_openai_enrichment_maps_all_three_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return resp(200, OPENAI_REPLY)

    install(monkeypatch, handler)
    provider = OpenAIEnrichmentProvider(api_key="sk-test")
    assert provider.summarize("Some transcript.") == "Two speakers discuss transcript quality."
    assert provider.key_points("Some transcript.") == ["Diarization matters", "Editing is expected"]
    quotes = provider.quotes("Some transcript.")
    assert quotes[0]["text"] == "Diarization matters most."

    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "gpt-4o-mini"
    # The prompt must demand verbatim quotes — paraphrased quotes are a
    # correctness/trust problem, not a style preference.
    prompt = captured["body"]["messages"][-1]["content"]
    assert "VERBATIM" in prompt
    assert "Some transcript." in prompt


def test_openai_requires_a_key() -> None:
    with pytest.raises(ProviderUnavailable, match="OPENAI_API_KEY"):
        OpenAIEnrichmentProvider(api_key=None).check_available()


def test_openai_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, lambda request: resp(500, "boom"))
    with pytest.raises(ProviderError, match="500"):
        OpenAIEnrichmentProvider(api_key="k").summarize("text")


def test_anthropic_enrichment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["key"] = request.headers.get("x-api-key")
        captured["version"] = request.headers.get("anthropic-version")
        return resp(
            200,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"summary": "Anthropic summary.", "key_points": ["a"], "quotes": []}
                        ),
                    }
                ]
            },
        )

    install(monkeypatch, handler)
    provider = AnthropicEnrichmentProvider(api_key="sk-ant-test")
    assert provider.summarize("Transcript.") == "Anthropic summary."
    assert provider.key_points("Transcript.") == ["a"]
    assert provider.quotes("Transcript.") == []
    assert captured["key"] == "sk-ant-test"
    assert captured["version"] == "2023-06-01"
    assert captured["body"]["system"], "system prompt must be sent"


def test_anthropic_requires_a_key() -> None:
    with pytest.raises(ProviderUnavailable, match="ANTHROPIC_API_KEY"):
        AnthropicEnrichmentProvider(api_key=None).check_available()


def test_anthropic_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, lambda request: resp(429, "rate limited"))
    with pytest.raises(ProviderError):
        AnthropicEnrichmentProvider(api_key="k").summarize("text")

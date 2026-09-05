"""Live-voice ingest (M1.5): service, HTTP endpoint, WebSocket protocol, assistant mock.

The WebSocket tests use starlette's TestClient websocket support, which speaks
the real ASGI websocket protocol — the same code path uvicorn serves.
"""

from __future__ import annotations

import io
import json
import tempfile
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.models import VoiceUtterance
from app.providers.assistant import MockAssistantProvider
from app.services.voice import VoiceError, pcm_s16le_to_wav, pcm_seconds, process_utterance


def wav_bytes(seconds: float = 4.0, sample_rate: int = 16_000) -> bytes:
    """A real WAV in memory (same synth as the batch-pipeline fixtures)."""
    from app.services.audio import synth_sample_wav

    with tempfile.TemporaryDirectory() as tmp:
        path = synth_sample_wav(Path(tmp) / "u.wav", seconds=seconds, sample_rate=sample_rate)
        return path.read_bytes()


def pcm_from_wav(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.readframes(w.getnframes())


# --------------------------------------------------------------------------- #
# Service layer
# --------------------------------------------------------------------------- #
def test_process_utterance_wav_end_to_end(db, tmp_path: Path, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "voice_dir", tmp_path)
    outcome = process_utterance(db, wav_bytes(4.0), audio_format="wav", source="http")

    u = outcome.utterance
    assert u.transcript
    assert u.asr_provider == "mock"
    assert u.detected_language == "en"
    assert u.audio_seconds == pytest.approx(4.0, abs=0.2)
    assert u.assistant_reply
    assert u.assistant_action == "none"  # generic acknowledgement
    assert u.processing_ms is not None and u.processing_ms >= 0
    assert u.audio_path and Path(u.audio_path).exists()
    assert db.get(VoiceUtterance, u.id) is not None


def test_process_utterance_pcm_gets_wav_header(db, tmp_path: Path, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "voice_dir", tmp_path)
    pcm = pcm_from_wav(wav_bytes(3.0))
    outcome = process_utterance(db, pcm, audio_format="pcm_s16le", sample_rate=16_000, source="ws")
    assert outcome.utterance.transcript
    stored = Path(outcome.utterance.audio_path)
    with wave.open(str(stored), "rb") as w:
        assert w.getframerate() == 16_000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2


def test_save_audio_false_keeps_no_file(db, tmp_path: Path, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "voice_dir", tmp_path)
    outcome = process_utterance(db, wav_bytes(2.0), save_audio=False)
    assert outcome.utterance.audio_path is None
    assert list(tmp_path.glob("*.wav")) == []


def test_guards_reject_bad_input(db) -> None:
    with pytest.raises(VoiceError, match="Empty"):
        process_utterance(db, b"")
    with pytest.raises(VoiceError, match="Unsupported format"):
        process_utterance(db, b"x", audio_format="mp3")
    with pytest.raises(VoiceError, match="Sample rate"):
        process_utterance(
            db, pcm_from_wav(wav_bytes(1.0)), audio_format="pcm_s16le", sample_rate=100
        )
    with pytest.raises(VoiceError, match="shorter than"):
        process_utterance(
            db,
            b"\x00\x00" * 1600,
            audio_format="pcm_s16le",
            sample_rate=16_000,  # 0.1 s
        )


def test_guard_rejects_overlong_utterance(db, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "voice_max_seconds", 1.0)
    with pytest.raises(VoiceError, match="over the"):
        process_utterance(db, wav_bytes(4.0), audio_format="wav")


def test_pcm_helpers() -> None:
    assert pcm_seconds(32_000, 16_000) == 1.0
    wrapped = pcm_s16le_to_wav(b"\x01\x02" * 100, 16_000)
    with wave.open(io.BytesIO(wrapped), "rb") as w:
        assert w.getnframes() == 100
    # Odd trailing byte is trimmed rather than crashing.
    assert pcm_s16le_to_wav(b"\x01\x02\x03", 16_000)


# --------------------------------------------------------------------------- #
# Assistant mock
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("said", "expect_action"),
    [
        ("What time is it?", "speak"),
        ("what's the date today", "speak"),
        ("help", "speak"),
        ("Repeat after me: hello world", "speak"),
        ("open EchoNeura docs", "navigate"),
        ("turn on the lights", "none"),
    ],
)
def test_mock_assistant_intents(said: str, expect_action: str) -> None:
    out = MockAssistantProvider().act(said)
    assert out.action == expect_action
    assert out.reply


def test_mock_assistant_echo_and_navigate_payloads() -> None:
    assistant = MockAssistantProvider()
    echo = assistant.act("repeat after me: buy milk")
    assert echo.data["echo"] == "buy milk"
    nav = assistant.act("open the weather forecast")
    assert nav.data["url"].startswith("https://")
    assert "weather" in nav.data["query"]


def test_mock_assistant_empty_transcript() -> None:
    out = MockAssistantProvider().act("   ")
    assert out.action == "speak"
    assert "didn't catch" in out.reply


def test_assistant_provider_openai_is_a_loud_hook(monkeypatch) -> None:
    """Selecting an unimplemented assistant must fail loudly, not silently mock."""
    from app.core.config import settings
    from app.providers.base import ProviderUnavailable
    from app.providers.registry import get_assistant_provider

    monkeypatch.setattr(settings, "assistant_provider", "openai")
    get_assistant_provider.cache_clear()
    with pytest.raises(ProviderUnavailable, match="reserved but not implemented"):
        get_assistant_provider()
    get_assistant_provider.cache_clear()


# --------------------------------------------------------------------------- #
# HTTP endpoint
# --------------------------------------------------------------------------- #
def test_post_utterance_returns_transcript_and_reply(client: TestClient, db) -> None:
    data = wav_bytes(4.0)
    resp = client.post(
        "/api/voice/utterance",
        files={"file": ("command.wav", io.BytesIO(data), "audio/wav")},
        data={"fmt": "wav"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["transcript"]
    assert body["assistant"]["reply"]
    assert body["source"] == "http"
    assert body["asr_provider"] == "mock"


def test_post_utterance_pcm_autodetect(client: TestClient, db) -> None:
    pcm = pcm_from_wav(wav_bytes(3.0))
    resp = client.post(
        "/api/voice/utterance",
        files={"file": ("audio.raw", io.BytesIO(pcm), "application/octet-stream")},
        data={"fmt": "auto", "sample_rate": "16000"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["transcript"]


def test_post_utterance_rejects_empty(client: TestClient, db) -> None:
    resp = client.post(
        "/api/voice/utterance",
        files={"file": ("empty.wav", io.BytesIO(b""), "audio/wav")},
        data={"fmt": "wav"},
    )
    assert resp.status_code == 400
    assert "Empty" in resp.json()["detail"]


def test_post_utterance_rejects_too_short(client: TestClient, db) -> None:
    resp = client.post(
        "/api/voice/utterance",
        files={
            "file": (
                "blip.wav",
                io.BytesIO(pcm_s16le_to_wav(b"\x00\x00" * 1600, 16_000)),
                "audio/wav",
            )
        },
        data={"fmt": "wav"},
    )
    assert resp.status_code == 422


def test_history_and_delete(client: TestClient, db) -> None:
    client.post(
        "/api/voice/utterance",
        files={"file": ("a.wav", io.BytesIO(wav_bytes(2.0)), "audio/wav")},
        data={"fmt": "wav"},
    )
    listed = client.get("/api/voice/utterances").json()
    assert listed["total"] >= 1
    uid = listed["items"][0]["id"]

    assert client.delete(f"/api/voice/utterances/{uid}").status_code == 204
    assert client.delete(f"/api/voice/utterances/{uid}").status_code == 404


# --------------------------------------------------------------------------- #
# WebSocket protocol
# --------------------------------------------------------------------------- #
def test_ws_full_session(client: TestClient, db) -> None:
    pcm = pcm_from_wav(wav_bytes(4.0))
    with client.websocket_connect(
        "/api/voice/stream?fmt=pcm_s16le&sample_rate=16000&source=pytest"
    ) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["protocol"] == 1
        assert ready["asr"] == "mock"
        assert ready["assistant"] == "mock"

        # Stream in ~0.25s frames like a real client would.
        frame = 16_000 * 2 // 4
        for i in range(0, len(pcm), frame):
            ws.send_bytes(pcm[i : i + frame])
        ws.send_text(json.dumps({"type": "stop"}))

        result = ws.receive_json()
        assert result["type"] == "result"
        u = result["utterance"]
        assert u["transcript"]
        assert u["source"] == "pytest"
        assert u["assistant"]["reply"]

        # Session survives for a second utterance.
        ws.send_bytes(pcm[: frame * 4])
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json()["type"] == "result"

        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json()["type"] == "pong"


def test_ws_discards_short_and_empty_utterances(client: TestClient, db) -> None:
    with client.websocket_connect("/api/voice/stream?fmt=pcm_s16le") as ws:
        ws.receive_json()  # ready

        ws.send_text(json.dumps({"type": "stop"}))  # nothing buffered
        msg = ws.receive_json()
        assert msg == {"type": "discarded", "reason": "empty", "seconds": 0.0}

        ws.send_bytes(b"\x00\x00" * 1600)  # 0.1 s
        ws.send_text(json.dumps({"type": "stop"}))
        msg = ws.receive_json()
        assert msg["type"] == "discarded" and msg["reason"] == "too_short"

        # Cancel drops the buffer without producing an utterance.
        ws.send_bytes(pcm_from_wav(wav_bytes(2.0)))
        ws.send_text(json.dumps({"type": "cancel"}))
        assert ws.receive_json()["type"] == "cancelled"
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json()["reason"] == "empty"


def test_ws_rejects_unknown_control_and_bad_json(client: TestClient, db) -> None:
    with client.websocket_connect("/api/voice/stream") as ws:
        ws.receive_json()
        ws.send_text("not json{{")
        assert "not valid JSON" in ws.receive_json()["message"]
        ws.send_text(json.dumps({"type": "teleport"}))
        assert "Unknown control type" in ws.receive_json()["message"]


def test_ws_rejects_unsupported_fmt(client: TestClient, db) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/voice/stream?fmt=mp3") as ws,
    ):
        ws.receive_json()


def test_ws_overflow_is_fatal(client: TestClient, db, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "voice_max_seconds", 1.0)
    with client.websocket_connect("/api/voice/stream?fmt=pcm_s16le&sample_rate=16000") as ws:
        ws.receive_json()
        # Server cap = max_seconds * rate * 2 bytes + 64 KiB slack; exceed it.
        cap = int(settings.voice_max_seconds * 16_000 * 2) + 65_536
        ws.send_bytes(b"\x00" * (cap + 1_024))
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["fatal"] is True


def test_voice_rows_appear_in_history(client: TestClient, db) -> None:
    pcm = pcm_from_wav(wav_bytes(2.0))
    with client.websocket_connect("/api/voice/stream?fmt=pcm_s16le&source=ws") as ws:
        ws.receive_json()
        ws.send_bytes(pcm)
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json()["type"] == "result"
    history = client.get("/api/voice/utterances").json()
    assert any(item["source"] == "ws" for item in history["items"])

"""Live-voice endpoints (M1.5).

    POST /api/voice/utterance        one-shot: audio file -> transcript + reply
    WS   /api/voice/stream           streaming session (the Echo Dot path)
    GET  /api/voice/utterances       recent history (UI + debugging)
    DEL  /api/voice/utterances/{id}  prune a row (and its stored audio)

WebSocket protocol (v1) — also documented in docs/device/voice-protocol.md:

    connect   ws(s)://host/api/voice/stream?fmt=pcm_s16le&sample_rate=16000
              &language=auto&source=ws
    server    {"type":"ready","protocol":1,...}
    client    binary frames  -> raw audio (fmt/sample_rate per the query string)
    client    {"type":"stop"}   -> finalize utterance
              {"type":"cancel"} -> discard buffered audio
              {"type":"ping"}   -> keepalive
    server    {"type":"result","utterance":{...}}   after each stop
              {"type":"pong"}
              {"type":"error","message":...,"fatal":bool}
              {"type":"discarded","reason":"too_short","seconds":0.21}

    The connection stays open for many utterances (hold-to-talk loops); a
    fatal error or client disconnect ends the session.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, get_db
from app.models import VoiceUtterance
from app.providers.base import ProviderError, ProviderUnavailable
from app.providers.registry import get_asr_provider, get_assistant_provider
from app.services.voice import (
    SUPPORTED_FORMATS,
    VoiceError,
    pcm_seconds,
    process_utterance,
    utterance_to_dict,
)

logger = logging.getLogger(__name__)
router = APIRouter()

PROTOCOL_VERSION = 1
MAX_BINARY_BYTES = 60 * 1024 * 1024  # absurd-upper guard vs memory abuse


# --------------------------------------------------------------------------- #
# HTTP one-shot
# --------------------------------------------------------------------------- #
@router.post("/utterance", status_code=status.HTTP_201_CREATED)
async def post_utterance(
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File(description="WAV file or raw pcm_s16le audio")],
    fmt: Annotated[str, Form()] = "wav",
    sample_rate: Annotated[int, Form()] = settings.voice_sample_rate,
    language: Annotated[str, Form()] = "auto",
    save_audio: Annotated[bool, Form()] = True,
) -> dict:
    """Synchronous utterance: audio in, transcript + assistant reply out."""
    audio = await file.read()
    await file.close()
    return await run_in_threadpool(
        _process_or_http_error,
        db,
        audio,
        audio_format=_fmt_from(fmt, file.filename, file.content_type),
        sample_rate=sample_rate,
        language=language,
        source="http",
        save_audio=save_audio,
    )


def _fmt_from(fmt: str, filename: str | None, content_type: str | None) -> str:
    if fmt and fmt != "auto":
        return fmt
    name = (filename or "").lower()
    if name.endswith(".wav") or (content_type or "") == "audio/wav":
        return "wav"
    if name.endswith((".pcm", ".raw")) or (content_type or "") == "application/octet-stream":
        return "pcm_s16le"
    return "wav"


def _process_or_http_error(
    db: Session,
    audio: bytes,
    *,
    audio_format: str,
    sample_rate: int,
    language: str,
    source: str,
    save_audio: bool,
) -> dict:
    try:
        outcome = process_utterance(
            db,
            audio,
            audio_format=audio_format,
            sample_rate=sample_rate,
            language=language,
            source=source,
            save_audio=save_audio,
        )
    except VoiceError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return utterance_to_dict(outcome.utterance)


# --------------------------------------------------------------------------- #
# WebSocket streaming session
# --------------------------------------------------------------------------- #
@router.websocket("/stream")
async def voice_stream(websocket: WebSocket) -> None:
    params = websocket.query_params
    fmt = params.get("fmt", "pcm_s16le")
    try:
        sample_rate = int(params.get("sample_rate", settings.voice_sample_rate))
    except ValueError:
        sample_rate = settings.voice_sample_rate
    language = params.get("language", "auto")
    source = params.get("source", "ws")[:16]

    if fmt not in SUPPORTED_FORMATS:
        await websocket.close(code=1008, reason=f"unsupported fmt {fmt!r}")
        return

    await websocket.accept()
    try:
        asr = get_asr_provider()
        asr.check_available()
        assistant = get_assistant_provider()
        assistant.check_available()
    except ProviderUnavailable as exc:
        await _send(websocket, {"type": "error", "message": str(exc), "fatal": True})
        await websocket.close(code=1011)
        return

    await _send(
        websocket,
        {
            "type": "ready",
            "protocol": PROTOCOL_VERSION,
            "fmt": fmt,
            "sample_rate": sample_rate,
            "language": language,
            "asr": asr.name,
            "assistant": assistant.name,
            "max_seconds": settings.voice_max_seconds,
            "min_seconds": settings.voice_min_seconds,
        },
    )

    buffer = bytearray()
    max_bytes = (
        int(settings.voice_max_seconds * sample_rate * 2) + 65_536
        if fmt == "pcm_s16le"
        else MAX_BINARY_BYTES
    )

    while True:
        try:
            message = await websocket.receive()
        except WebSocketDisconnect:
            break

        if message.get("type") == "websocket.disconnect":
            break

        if (chunk := message.get("bytes")) is not None:
            if len(buffer) + len(chunk) > max_bytes:
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "fatal": True,
                        "message": (
                            f"Audio exceeds the {settings.voice_max_seconds:.0f}s per-utterance "
                            "limit. Send a stop, or use the batch pipeline for long recordings."
                        ),
                    },
                )
                await websocket.close(code=1009)
                return
            buffer.extend(chunk)
            continue

        raw_text = message.get("text")
        if raw_text is None:
            continue
        try:
            control = json.loads(raw_text)
        except json.JSONDecodeError:
            await _send(websocket, {"type": "error", "message": "Control frame is not valid JSON"})
            continue

        kind = control.get("type")
        if kind == "ping":
            await _send(websocket, {"type": "pong"})
        elif kind == "cancel":
            seconds = pcm_seconds(len(buffer), sample_rate) if fmt == "pcm_s16le" else None
            buffer.clear()
            await _send(websocket, {"type": "cancelled", "discarded_seconds": seconds})
        elif kind == "stop":
            if not buffer:
                await _send(
                    websocket,
                    {"type": "discarded", "reason": "empty", "seconds": 0.0},
                )
                continue
            audio = bytes(buffer)
            buffer.clear()
            seconds = pcm_seconds(len(audio), sample_rate) if fmt == "pcm_s16le" else None
            if seconds is not None and seconds < settings.voice_min_seconds:
                await _send(
                    websocket,
                    {"type": "discarded", "reason": "too_short", "seconds": round(seconds, 2)},
                )
                continue
            # ASR runs in a thread: the event loop keeps serving other clients.
            db = SessionLocal()
            try:
                outcome = await run_in_threadpool(
                    process_utterance,
                    db,
                    audio,
                    audio_format=fmt,
                    sample_rate=sample_rate,
                    language=language,
                    source=source,
                )
                await _send(
                    websocket,
                    {"type": "result", "utterance": utterance_to_dict(outcome.utterance)},
                )
            except VoiceError as exc:
                await _send(
                    websocket,
                    {"type": "error", "message": exc.message, "fatal": exc.fatal},
                )
                if exc.fatal:
                    await websocket.close(code=1009)
                    return
            except (ProviderUnavailable, ProviderError) as exc:
                fatal = isinstance(exc, ProviderUnavailable)
                await _send(websocket, {"type": "error", "message": str(exc), "fatal": fatal})
                if fatal:
                    await websocket.close(code=1011)
                    return
            except Exception as exc:
                logger.exception("Utterance processing failed")
                await _send(
                    websocket,
                    {"type": "error", "message": f"Internal error: {exc}", "fatal": False},
                )
            finally:
                db.close()
        else:
            await _send(websocket, {"type": "error", "message": f"Unknown control type {kind!r}"})

    if buffer:
        logger.info("Voice session ended with %d unsent audio bytes discarded", len(buffer))


async def _send(websocket: WebSocket, payload: dict) -> None:
    await websocket.send_text(json.dumps(payload, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
@router.get("/utterances")
def list_utterances(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    rows = db.scalars(
        select(VoiceUtterance)
        .order_by(VoiceUtterance.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {"total": len(rows), "items": [utterance_to_dict(u) for u in rows]}


@router.delete("/utterances/{utterance_id}", status_code=204)
def delete_utterance(utterance_id: str, db: Annotated[Session, Depends(get_db)]) -> None:
    row = db.get(VoiceUtterance, utterance_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utterance not found")
    if row.audio_path:
        Path(row.audio_path).unlink(missing_ok=True)
    db.delete(row)
    db.commit()

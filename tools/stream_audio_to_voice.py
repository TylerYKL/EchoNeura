#!/usr/bin/env python3
"""Stream an audio file into EchoNeura's live-voice WebSocket, like a mic client.

This is the reference implementation of the voice-stream protocol
(docs/device/voice-protocol.md) — the rooted Echo Dot client (or a Raspberry Pi
satellite, or CI) does exactly this, just with mic frames instead of a file.

Usage:
    # stream the demo clip through the local API, pseudo-realtime
    python tools/stream_audio_to_voice.py data/samples/demo.wav

    # through the Next.js proxy (what the browser uses)
    python tools/stream_audio_to_voice.py data/samples/demo.wav \
        --url ws://127.0.0.1:3000/api/voice/stream

    # raw PCM from a mic-capture pipe (arecord -f S16_LE -r 16000 -c 1)
    arecord -f S16_LE -r 16000 -c 1 -d 5 - | \
        python tools/stream_audio_to_voice.py - --fmt pcm_s16le

Requires the `websockets` package (already in backend/.venv via uvicorn[standard]).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("This tool needs the 'websockets' package: pip install websockets")

FRAME_SECONDS = 0.25


def read_audio(path: str, fmt: str) -> tuple[bytes, int]:
    """Return (raw pcm_s16le mono bytes, sample_rate) for wav/-/pcm input."""
    if fmt == "pcm_s16le":
        data = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
        return data, 16_000
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(path).read_bytes()
    with wave.open(__import__("io").BytesIO(data), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            sys.exit("Send mono 16-bit WAV (ffmpeg -i in.mp3 -ac 1 -ar 16000 -f wav out.wav)")
        return w.readframes(w.getnframes()), w.getframerate()


async def stream(url: str, pcm: bytes, sample_rate: int, realtime: bool, language: str) -> int:
    frame_bytes = int(sample_rate * 2 * FRAME_SECONDS)
    total_frames = (len(pcm) + frame_bytes - 1) // frame_bytes
    exit_code = 0

    query = f"?fmt=pcm_s16le&sample_rate={sample_rate}&language={language}&source=tool"
    async with websockets.connect(url + query, max_size=16 * 1024 * 1024) as ws:
        ready = json.loads(await ws.recv())
        if ready.get("type") != "ready":
            print(f"server refused session: {ready}", file=sys.stderr)
            return 1
        print(
            f"connected: asr={ready['asr']} assistant={ready['assistant']} "
            f"protocol=v{ready['protocol']}"
        )

        started = time.monotonic()
        for i in range(0, len(pcm), frame_bytes):
            await ws.send(pcm[i : i + frame_bytes])
            if realtime:
                target = started + (i + frame_bytes) / (sample_rate * 2)
                delay = target - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)

        duration = len(pcm) / (sample_rate * 2)
        print(f"sent {duration:.2f}s of audio in {total_frames} frames; waiting for result…")
        await ws.send(json.dumps({"type": "stop"}))

        while True:
            message = json.loads(await ws.recv())
            kind = message.get("type")
            if kind == "result":
                u = message["utterance"]
                print("\n--- transcript ---------------------------------")
                print(u["transcript"])
                print("--- assistant ------------------------------------")
                print(f"action: {u['assistant']['action']}")
                print(f"reply : {u['assistant']['reply']}")
                if u["assistant"]["data"]:
                    print(f"data  : {json.dumps(u['assistant']['data'], ensure_ascii=False)}")
                print(
                    f"\n({u['audio_seconds']:.1f}s audio, {u['processing_ms']}ms processing, "
                    f"id={u['id']})"
                )
                break
            if kind == "discarded":
                print(f"discarded: {message.get('reason')} ({message.get('seconds')}s)", file=sys.stderr)
                exit_code = 2
                break
            if kind == "error":
                print(f"error: {message.get('message')}", file=sys.stderr)
                exit_code = 1
                if message.get("fatal"):
                    break
            else:
                print(f"<- {message}")

        await ws.send(json.dumps({"type": "ping"}))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", help="WAV file, raw PCM file, or '-' for stdin")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/api/voice/stream")
    parser.add_argument("--fmt", default="wav", choices=["wav", "pcm_s16le"])
    parser.add_argument("--language", default="auto")
    parser.add_argument(
        "--fast", action="store_true", help="send as fast as possible (default: pseudo-realtime)"
    )
    args = parser.parse_args()

    pcm, sample_rate = read_audio(args.audio, args.fmt)
    if not pcm:
        print("no audio to send", file=sys.stderr)
        return 1
    return asyncio.run(stream(args.url, pcm, sample_rate, not args.fast, args.language))


if __name__ == "__main__":
    raise SystemExit(main())

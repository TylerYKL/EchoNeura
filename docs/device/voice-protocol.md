# EchoNeura live-voice protocol (v1)

One WebSocket carries a whole conversation session: raw audio in, transcript +
assistant action out. It is deliberately minimal so a rooted Echo Dot, a
Raspberry Pi satellite, a browser, or a CI script can all be the same client.
Reference implementations: `frontend/lib/voice-ws.ts` + `components/VoiceClient.tsx`
(browser), `tools/stream_audio_to_voice.py` (CLI).

## Endpoints

| Endpoint | Purpose |
|---|---|
| `WS /api/voice/stream` | Live streaming session (this document) |
| `POST /api/voice/utterance` | One-shot multipart upload of a complete clip (`file` + `fmt=wav|auto`, `sample_rate`, `language`) → `201` with the same utterance JSON |
| `GET /api/voice/utterances?limit=` | Recent utterances, newest first |
| `DELETE /api/voice/utterances/{id}` | Delete one utterance (and its stored audio) |

## Connecting

```
GET /api/voice/stream?fmt=pcm_s16le&sample_rate=16000&language=auto&source=dot
Upgrade: websocket
```

Query parameters (all optional):

| Param | Default | Notes |
|---|---|---|
| `fmt` | `pcm_s16le` | Only `pcm_s16le` today; `wav` accepted for the one-shot endpoint. Anything else → immediate close (code 1008). |
| `sample_rate` | `16000` | Hz, mono, signed 16-bit little-endian. Valid 8000–48000 (server rejects otherwise with 1008). Browsers that ignore the `AudioContext({sampleRate})` hint must send their real context rate here. |
| `language` | `auto` | Hint passed to the ASR provider. |
| `source` | `ws` | Free-form tag stored with the utterance (`browser`, `dot`, `pi`, `tool`, …) — handy for filtering history. |

On accept the server sends exactly one **ready** message:

```json
{"type": "ready", "protocol": 1, "fmt": "pcm_s16le", "sample_rate": 16000,
 "asr": "mock", "assistant": "mock", "max_seconds": 300.0, "min_seconds": 0.4}
```

## Client → server messages

- **Binary frames**: raw PCM audio, appended to the session buffer. Any frame
  size; ~0.25 s chunks (8 KB at 16 kHz) are what the browser client sends.
- **Text frames**: JSON control messages:

```json
{"type": "stop"}     // end of utterance → server processes the buffer
{"type": "cancel"}   // drop the buffer without processing
{"type": "ping"}     // keepalive → {"type": "pong"}
```

Unknown control types or invalid JSON get a non-fatal `error` message; the
session survives.

## Server → client messages

```json
{"type": "result", "utterance": { ... }}   // after a successful "stop"
{"type": "discarded", "reason": "empty|too_short", "seconds": 3.2}
{"type": "cancelled", "discarded_seconds": 3.2}
{"type": "pong"}
{"type": "error", "message": "...", "fatal": false}
```

`fatal: true` means the server closes the socket right after (e.g. buffer
overflow past `max_seconds` — close code 1009). Non-fatal errors leave the
session open.

### `utterance` object

```json
{
  "id": "uuid",
  "created_at": "2026-09-05T14:04:05Z",
  "source": "dot",
  "audio_format": "pcm_s16le",
  "sample_rate": 16000,
  "audio_seconds": 4.02,
  "language": "auto",
  "detected_language": "en",
  "asr_provider": "mock",
  "transcript": "so the goal here is to turn a raw recording into …",
  "confidence": 0.97,
  "assistant": {
    "provider": "mock",
    "reply": "I heard: \"…\". This is the mock assistant — set ECHONEURA_ASSISTANT_PROVIDER …",
    "action": "speak|navigate|none|…",
    "data": {"echo": "…"}
  },
  "processing_ms": 312,
  "audio_path": "/abs/path/data/voice/2026/09/05/<uuid>.wav"
}
```

`assistant.action` is an open vocabulary for now — the mock defines `speak`,
`navigate`, `none`; a real provider (ADR 0004) may add intents. Clients should
treat unknown actions as display-only.

## Session semantics

- One socket = one session = **many utterances**. After `result`/`discarded`
  the buffer resets and you may immediately stream the next utterance.
- Disconnecting with bytes still buffered **discards** them (logged, not
  processed) — always send `stop` before closing if you want the audio kept.
- The server enforces `ECHONEURA_VOICE_MAX_SECONDS` (default 300) per utterance
  and `ECHONEURA_VOICE_MIN_SECONDS` (default 0.4) to drop accidental blips.
- Audio is saved as WAV under `ECHONEURA_VOICE_DIR` (`data/voice/YYYY/MM/DD/`)
  and deleted with the utterance row.

## Typical client loop (pseudocode)

```
ws = connect(url)                    # see query params above
expect {"type": "ready"}
loop:
    while recording:                 # e.g. wake-word triggered or hold-to-talk
        ws.send(pcm_chunk)           # binary, ~0.25 s per frame
    ws.send('{"type":"stop"}')
    msg = ws.receive()
    if msg.type == "result":   handle(msg.utterance)
    if msg.type == "discarded": notify_user(msg.reason)
    if msg.type == "error" and msg.fatal: reconnect()
```

`tools/stream_audio_to_voice.py` is this loop with a WAV file as the mic —
useful for testing from any machine:

```bash
python tools/stream_audio_to_voice.py data/samples/demo.wav --fast
```

## Versioning

`ready.protocol` is `1`. Breaking changes bump it; additive fields do not.
Clients should check `protocol` on connect and refuse loudly on mismatch.

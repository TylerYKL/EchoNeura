# ADR 0003 — Provider abstraction: mock / local / cloud behind one interface

**Status:** accepted · **Date:** 2026-09-05 · **Milestone:** M1

## Context

The plan's model strategy has real tension:

- **Local models** (faster-whisper, pyannote) have no per-minute cost but need a
  GPU; the dev sandbox has 2 CPUs / 3 GB RAM / no GPU.
- **Cloud APIs** (AssemblyAI, Deepgram, Groq, OpenAI) work anywhere but cost
  money per audio-hour and send user audio to third parties.
- Demos, CI and frontend work need *something real to run against* without keys,
  GPU, or spend.

## Decision

Every AI stage sits behind a small interface in `app/providers/base.py`
(`ASRProvider`, `DiarizationProvider`, `EnrichmentProvider`) with three adapter
families, selected purely by env vars (`ECHONEURA_ASR_PROVIDER`, etc.):

| Family | Implementations | Cost | Runs here? |
|---|---|---|---|
| `mock` | deterministic offline transcripts derived from the file's real ffmpeg-probed duration, two speakers, word timings | $0 | ✅ default |
| `local` | `faster_whisper` (CTranslate2), `pyannote` diarization | GPU/CPU time | code complete, gated on deps |
| `cloud` | `assemblyai`, `deepgram`, `groq_whisper`, `openai`, `anthropic` — pure httpx, no vendor SDKs | per-minute fees | ✅ with keys |

Key design points:

- **Canonical intermediate types** (`ASRResult`, `ASRSegment`, `ASRWord`,
  `DiarizationResult`) — nothing outside `app/providers` may know a vendor's
  payload shape. Vendor units are normalized at the edge (AssemblyAI ms → s).
- **Two diarization topologies are first-class**: vendors that bundle diarization
  (AssemblyAI/Deepgram/mock return speaker-labelled segments) and split setups
  (faster-whisper + pyannote), reconciled by weighted time-overlap voting in
  `app/pipeline/merge.py` — including splitting an ASR line at an internal
  speaker change when word timestamps allow.
- **Loud, actionable misconfiguration**: selecting a cloud provider without its
  key raises `ProviderUnavailable` naming the exact env var; `/api/health`
  reports `missing_config` and the UI banner shows it before the first upload.
- **Retry classification**: `ProviderError` (retryable: 429/5xx/transport) vs
  `ProviderUnavailable` (not retryable: missing key/dep, oversized file) drives
  the worker's retry policy (ADR 0002).
- **Cloud adapters are unit-tested with mocked httpx transports** against
  shape-accurate vendor payloads — parsing bugs are caught without spend.

## Consequences

- Positive: the entire product runs offline (`make dev` → upload → SRT), so CI,
  demos, and frontend development never touch a paid API.
- Positive: switching production ASR is a config change, not a refactor.
- Negative: the abstraction must serve the lowest common denominator (e.g.
  Groq has no diarization) — handled by the merge stage's fallbacks rather than
  by leaking vendor specifics upward.
- Negative: mock transcripts are synthetic; before trusting a cloud provider in
  production, run one real file per provider and compare (checklist in
  `docs/PLAN.md` M0).

## Cost model (per audio hour, list prices — verify before committing)

| Provider | ~$/hr | Diarization | Word ts | Notes |
|---|---|---|---|---|
| Groq whisper-large-v3 | ≈ $1.5–4 | ❌ | ✅ | fastest cheap ASR; 100 MB request cap |
| Deepgram nova-3 | ≈ $3–6 | ✅ | ✅ | synchronous, low latency |
| AssemblyAI best | ≈ $12–18 | ✅ | ✅ | async upload+poll; strongest formatting |
| faster-whisper local | GPU time only | via pyannote | ✅ | large-v3 ≈ 10 GB VRAM fp16 |
| pyannote local | GPU time only | ✅ | — | gated HF model, needs token |

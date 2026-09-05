# EchoNeura — AI Audio Content Studio

Upload a recording → get a **speaker-separated transcript you can correct inline** →
export **SRT / WebVTT / TXT / Markdown / JSON**. Summaries, quote cards and clips
arrive in later milestones.

**M1 is implemented and runnable end-to-end with zero API keys**: the default
`mock` provider derives a deterministic transcript from the *real* ffmpeg-probed
duration of your file, so every stage (probe → transcribe → diarize → merge →
persist → edit → export) genuinely executes. Swap in a real ASR provider with one
env var — no code changes.

```
┌─────────────┐   POST /api/jobs    ┌──────────────────────────────────────────┐
│  Next.js 16 │ ──────────────────▶ │ FastAPI                                   │
│  (web UI)   │   /api/* proxied    │  ├─ jobs API (upload/edit/export)         │
└─────────────┘                     │  └─ worker (DB-backed queue, in-process   │
                                    │     or standalone)                        │
                                    │       preprocess → transcribe → diarize   │
                                    │       → merge speakers → persist          │
                                    └──────────────┬───────────────────────────┘
                                                   │ provider interfaces
                                    ┌──────────────▼───────────────────────────┐
                                    │ mock │ faster-whisper+pyannote │ AssemblyAI│
                                    │      │ (local GPU)             │ Deepgram  │
                                    │      │                         │ Groq      │
                                    └───────────────────────────────────────────┘
```

## Quickstart

Requirements: **Python 3.11+** and **Node 20+**. No Docker, no Redis, no GPU,
no root, no API keys.

```bash
make setup     # venv + backend deps + frontend deps + .env
make dev       # API on :8000 (docs at /api/docs) · web UI on :3000
make sample    # optional: generate data/samples/demo.wav to upload
```

Then open http://localhost:3000, drop in an audio/video file, and watch the
pipeline stages advance. The job page lets you:

- play the audio and click any timestamp to seek there
- click a transcript line to correct it (⌘/Ctrl+Enter saves, Esc discards;
  edits are batched and autosaved)
- move a line to a different speaker, rename speakers, recolor them
- filter by speaker / search / show only edited lines
- export SRT, WebVTT, TXT, Markdown or JSON — with or without speaker names,
  adjustable subtitle line width
- inspect the full pipeline log, cancel, reprocess, delete

## Useful commands

```bash
make test          # backend test suite (192 tests, offline, ~25 s)
make lint          # ruff + tsc
make format        # ruff fix/format
make dev-worker    # standalone worker (set ECHONEURA_WORKER_IN_PROCESS=false)
make audit         # npm audit + outdated python deps
```

## Using a real ASR provider

All configuration lives in `.env` (see `.env.example` for the full annotated
contract). Examples:

```bash
# Cheapest real ASR (no diarization)
ECHONEURA_ASR_PROVIDER=groq_whisper
ECHONEURA_GROQ_API_KEY=gsk_...

# ASR + diarization in one call
ECHONEURA_ASR_PROVIDER=assemblyai        # or: deepgram
ECHONEURA_ASSEMBLYAI_API_KEY=...

# Fully self-hosted (GPU box; ~2 GB of deps)
pip install faster-whisper torch pyannote.audio
ECHONEURA_ASR_PROVIDER=faster_whisper
ECHONEURA_DIARIZATION_PROVIDER=pyannote
ECHONEURA_HUGGINGFACE_TOKEN=hf_...       # pyannote models are gated
```

`GET /api/health` (and the banner on the home page) tells you exactly which
providers are active and which env vars are missing — before you waste an
upload on a misconfigured pipeline.

## Repository layout

```
backend/
  app/
    main.py            FastAPI app + lifespan (DB init, in-process worker)
    worker.py          DB-backed job queue: claim, lease, retry, recovery
    models.py          Job / Transcript / Speaker / Segment / JobEvent
    schemas.py         Pydantic API contract
    api/               routers: jobs (upload/edit/export), health
    pipeline/          jobs.py (state machine), merge.py (speaker assignment),
                       srt.py (subtitle/export builders), timefmt.py
    providers/         base.py (interfaces) + mock / local / cloud / registry
    services/audio.py  ffmpeg probe/convert via imageio-ffmpeg static build
  tests/               unit + integration (mocked HTTP for cloud adapters)
frontend/
  app/                 Next.js App Router: / and /jobs/[id]
  components/          UploadDropzone, TranscriptView, SegmentRow, SpeakerPanel,
                       ExportPanel, JobProgress, AudioPlayer, …
  lib/                 api client (relative URLs), types (mirror of schemas.py),
                       hooks (polling), format helpers
docs/adr/              0001 stack · 0002 job queue · 0003 provider abstraction
docs/PLAN.md           milestone tracker (M0–M7) with acceptance criteria
tools/                 make_sample_audio.py
data/                  SQLite DB + uploads (gitignored)
```

## How it works (the parts that matter)

- **Queue without a broker.** Jobs are rows; claiming is an atomic conditional
  UPDATE with a lease, so crashes recover and two workers never double-process.
  Details and the Redis migration trigger: [ADR 0002](docs/adr/0002-job-queue.md).
- **Swappable AI.** `mock | local | cloud` adapters behind one interface;
  canonical segment/word types; vendor units normalized at the edge.
  [ADR 0003](docs/adr/0003-provider-abstraction.md).
- **Speaker assignment.** Weighted time-overlap voting, splitting ASR lines at
  internal speaker changes when word timestamps allow; inconclusive lines are
  flagged `UNKNOWN` rather than guessed or dropped (`app/pipeline/merge.py`).
- **Subtitle craft.** Cues capped at 2 lines × 42 chars, merged across small
  gaps within one speaker, de-overlapped by 1 ms, floored (never rounded) so a
  cue never starts before its audio (`app/pipeline/srt.py`, `timefmt.py`).
- **Edit provenance.** Every segment keeps `original_text`; `is_edited` marks
  human corrections; `revision` bumps on the transcript so exports can be cached
  later.

## Security & privacy notes

- Uploads are streamed to disk with a hard size cap; filenames are sanitized;
  stored files live under `data/uploads/{job_id}/` (gitignored, deletable via
  `DELETE /api/jobs/{id}`).
- Audio only reaches a third party if you configure a cloud provider — the mock
  and local providers never leave the machine.
- Next.js is pinned to a **CVE-2025-66478-patched** release (see ADR 0001);
  `make audit` must stay clean.
- Auth, per-user storage isolation and rate limits are deliberately **out of
  scope for M1** and land in M6 — do not expose this build publicly until then.

## Roadmap

See [docs/PLAN.md](docs/PLAN.md). M0 (decisions/scaffold) and M1 (core pipeline)
are complete; M2 (summaries) is next.

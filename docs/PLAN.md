# EchoNeura — Milestone Plan & Tracker

Source: the original project plan (audio → transcript → summary → quote cards).
This document converts it into verifiable acceptance criteria and tracks status.

**Legend:** ✅ done · 🔨 in progress · ⬜ not started

---

## M0 — Decisions & scaffold ✅

| Item | Status | Notes |
|---|---|---|
| Stack decision | ✅ | FastAPI + Next.js 16 — [ADR 0001](adr/0001-stack.md). Rails rejected: diarization/local ASR are Python-only, so Rails would add a second deployable without removing the Python sidecar. |
| Job queue decision | ✅ | DB-backed queue, no broker — [ADR 0002](adr/0002-job-queue.md). Redis deferred to M6 with an explicit migration trigger. |
| Provider strategy | ✅ | mock/local/cloud adapters — [ADR 0003](adr/0003-provider-abstraction.md), incl. cost model per audio-hour. |
| Repo layout, linting, tests, .env contract | ✅ | ruff + tsc strict; 192 offline tests; annotated `.env.example`. |
| Security baseline | ✅ | Next pinned to CVE-2025-66478-patched 16.3.4; `npm audit` = 0 vulns; upload sanitization; `.gitignore` keeps audio/DB/secrets out of git. |
| Deployment target | ⬜ | Plan lead: Railway. Deferred by decision — local preview first. Dockerfile + railway/render/fly configs land when we deploy (worker runs standalone via `ECHONEURA_WORKER_IN_PROCESS=false`). |

**Open item carried from the original plan:** reference screenshots were never
attached — if they define a target UI, re-share them and we diff the M1 UI
against them.

## M1 — Core pipeline ✅ (this release)

Acceptance criteria (all verified by tests and by hand against the live app):

- [x] **Upload**: drag-and-drop + file picker; streams to disk; rejects
      non-audio (415), empty files (400), oversize (413, cap 500 MB),
      unsupported language codes (422); hostile filenames sanitized.
- [x] **Preprocess**: ffmpeg probe for real duration/codec/rate (static build,
      no apt needed); normalizes to 16 kHz mono WAV only when a local model
      needs it; temp file cleaned up after success.
- [x] **Transcribe**: provider interface with 5 ASR adapters (mock,
      faster-whisper, AssemblyAI, Deepgram, Groq); word-level timestamps;
      language auto-detect or explicit (40+ codes); retry with backoff on
      transient vendor errors; fail-fast with the exact missing env var on
      misconfiguration.
- [x] **Diarize**: bundled (vendor) or standalone (pyannote/mock/none);
      weighted time-overlap merge; ASR lines spanning a speaker change are
      split at the boundary when word timings allow; inconclusive lines flagged
      `UNKNOWN`, never dropped.
- [x] **Persist**: Job → Transcript → Speakers → Segments + append-only
      JobEvent audit trail; reprocessing replaces (never duplicates) output.
- [x] **State machine & queue**: queued → preprocessing → transcribing →
      diarizing → merging → aligning → completed/failed/cancelled; atomic
      claim; lease-based crash recovery; cancel/reprocess/delete endpoints.
- [x] **Editor UI**: live progress with stage stepper; audio player with
      click-a-timestamp-to-seek; inline text correction (draft-commit model,
      batched autosave, revert-to-original per line); speaker reassignment per
      line; speaker rename + recolor; search / speaker filter / edited-only
      filter; long-transcript render cap with "show all".
- [x] **Export**: SRT, WebVTT, TXT, Markdown, JSON — from the *current edited*
      state; speaker-name prefix toggle; timestamp toggle; subtitle line-width
      control; broadcast-safe wrapping (2×42 default), cue de-overlap, floored
      millisecond timestamps; UTF-8 safe for all 40+ languages.
- [x] **Observability**: `/api/health` reports active providers, missing
      config, ffmpeg resolution, queue depth; home-page banner surfaces
      degraded state before the first upload.

**Known limits (deliberate, M1):** single user, no auth; SQLite single writer
(`worker_max_concurrent_jobs=1`); transcript polling (1.2 s) instead of SSE;
no chunking of files beyond vendor size limits (Groq 100 MB → clear error
suggesting AssemblyAI/Deepgram).

## M2 — AI enrichment ⬜ (next)

Summary, key points, pull-quote extraction. Schema fields (`summary`,
`key_points_json`, `quotes_json`), the `EnrichmentProvider` interface, and
mock/OpenAI/Anthropic adapters **already exist and are tested** — M2 is the
UI + pipeline stage + feature flag (`ECHONEURA_ENABLE_SUMMARY`), not new
plumbing.

Acceptance criteria:
- [ ] "Generate summary" stage after transcript completion (idempotent, manual
      re-generate keeps the human-edited transcript untouched)
- [ ] Summary + 3–6 key points panel on the job page; editable, provenance-
      tracked like segments
- [ ] Quotes are **verbatim spans** linked to segment ids (no paraphrasing);
      quote picker seeds M3
- [ ] Provider cost guard: refuse enrichment over N audio-minutes without
      confirmation (configurable)
- [ ] Language parity: summary in the transcript's detected language

## M3 — Quote cards ⬜

PNG/PDF quote-card generator (templates, fonts, branding, 1:1/4:5/16:9).
Plan: server-side rendering (Playwright or Pillow+HTML template). Gated by
`ECHONEURA_ENABLE_QUOTE_CARDS`. Depends on M2 quote picker.

## M4 — Speaker memory & clips ⬜

- Speaker rename memory across jobs (voiceprint or manual mapping) — note:
  `Speaker.speaker_key` is already immutable provenance for this.
- Clip selection UI (in/out points on the waveform), clip export via ffmpeg.

## M5 — Transcription memory ⬜

Content-hash lookup to reuse paid ASR results for re-uploaded identical audio
(`file_sha256` column + provider/model fingerprint).

## M6 — Auth, billing, scale ⬜

- Auth (magic link/OAuth), per-user job isolation, storage quotas
- Rate limiting + abuse caps on upload
- **Queue migration trigger from ADR 0002**: >2 concurrent workers or
  multi-host ⇒ Postgres `SKIP LOCKED` (same code) or Redis/arq/Celery
- Deployment: Dockerfile (slim, torch optional), Railway/Render config,
  standalone worker container, managed Postgres, object storage for uploads

## M7 — Multilingual UI + translator ⬜

i18n for the app shell; transcript translation stage (gated by
`ECHONEURA_ENABLE_TRANSLATOR`); bilingual SRT export.

---

## Verification checklist for "real provider" cutover

Before pointing production at a paid provider, run one real file per candidate:

1. `make sample` → upload with the provider configured → confirm stage log
2. Compare segment count/word timings against mock expectations (sanity, not
   equality)
3. Check a 2-speaker conversation: are speaker changes at the right places?
4. Export SRT into a video editor (or `ffplay`) — spot-check sync at start,
   middle, end
5. Watch `/api/health` `missing_config` stay empty and `npm audit` stay clean

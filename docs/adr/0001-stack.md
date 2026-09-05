# ADR 0001 — Application stack: FastAPI (Python) + Next.js (TypeScript)

**Status:** accepted · **Date:** 2026-09-05 · **Milestone:** M0

## Context

The project plan left the stack open between two options:

1. FastAPI (Python) + Next.js (TypeScript)
2. Rails (Ruby) + React

The deciding constraints come from the product, not from taste:

- The pipeline's core value lives in Python-only libraries: `pyannote.audio`
  (speaker diarization), `faster-whisper` (local ASR), `torch`, and the ffmpeg
  ecosystem. The plan's own model strategy lists these as the self-hosted path.
- The heavy work is I/O-bound (vendor APIs) *and* occasionally CPU/GPU-bound
  (local models). We need async HTTP plus a task queue that can also shell out
  to ffmpeg and load models.
- The UI is an editor: optimistic inline edits, polling job state, streaming
  uploads with progress. It benefits from a typed frontend contract.

## Decision

**FastAPI (Python 3.11+) for the API and pipeline; Next.js 16 (React 19,
TypeScript) for the web UI.** Rails is rejected.

Supporting choices inside that decision:

| Concern | Choice | Why |
|---|---|---|
| Web framework | FastAPI + Pydantic v2 | Async, typed request/response schemas, automatic OpenAPI docs at `/api/docs` |
| ORM | SQLAlchemy 2.0 | Works on SQLite (dev) and Postgres (prod) with identical code |
| Database | SQLite (WAL) → Postgres via one env var | M1 must run with zero infra; `DATABASE_URL` is the only change needed later |
| Frontend | Next.js App Router, client-heavy pages | The editor is an interactive client app; SSR is used only for first paint + server-side health probe |
| Styling | Tailwind CSS v4 | No design-system dependency for a tool UI |
| Lint/format | ruff (Python), tsc strict (TS) | One fast tool each; enforced in CI |
| Runtime pin | Next.js **16.3.4**, React **19.1.2** | `next@15.5.4` is vulnerable to **CVE-2025-66478** ("React2Shell", CVSS 10.0 RCE via the RSC protocol; fixed in 15.5.7+/16.0.7+). We pin the latest patched line and `npm audit` must stay at 0 vulnerabilities |

## Why not Rails + React

- Diarization and local ASR would still require a Python sidecar service, so
  Rails adds a second language and a second deployable without removing one.
- Ruby is not installed in the build/CI sandbox, so nothing could be verified
  end-to-end here.
- The team-of-one maintenance surface is smaller with one backend language that
  matches the ML ecosystem.

## Consequences

- Positive: providers (`pyannote`, `faster-whisper`, vendor SDKs) are in-process
  imports, not network hops. One language for API, worker, and tooling.
- Positive: the OpenAPI schema doubles as the frontend's type source
  (`lib/types.ts` mirrors `backend/app/schemas.py`).
- Negative: Python deploys are heavier than a single static binary; mitigated by
  slim Docker images and keeping torch optional (installed only when
  `ECHONEURA_ASR_PROVIDER=faster_whisper`).
- Negative: two dev servers for local work; mitigated by `make dev` and the
  Next rewrite proxy so the browser only ever sees one origin.

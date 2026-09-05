# ADR 0002 — Job queue: database-backed, no broker (yet)

**Status:** accepted · **Date:** 2026-09-05 · **Milestone:** M1

## Context

Transcription is a long-running background job (seconds to minutes). The plan
proposed "Redis + worker". M1 must run on a laptop, in this sandbox (no Docker,
no Redis), and later on a single Railway/Render container.

## Decision

**A database-backed queue polled by an asyncio worker**, with Redis/Celery
deferred until a concrete scaling need appears (M6+).

Mechanics (see `backend/app/worker.py`):

- Jobs are rows; `status` is the state machine
  (`queued → preprocessing → transcribing → diarizing → merging → aligning →
  completed | failed | cancelled`).
- **Claiming** is an atomic conditional UPDATE:
  `UPDATE jobs SET worker_id=…, lease_expires_at=… WHERE id=:id AND status='queued'`.
  Two racing workers cannot both win — the loser matches 0 rows. This is the
  portable equivalent of Postgres `FOR UPDATE SKIP LOCKED` and also works on
  SQLite under WAL.
- **Leases** (`lease_expires_at`, default 15 min) make crashed workers' jobs
  recoverable: any poller requeues jobs whose lease expired while non-terminal.
- **Retries**: retryable errors requeue with exponential backoff
  (`attempts < max_attempts`); configuration errors (`ProviderUnavailable`) fail
  immediately — retrying a missing API key just burns time.
- **Deployment flexibility**: `ECHONEURA_WORKER_IN_PROCESS=true` runs the worker
  as an asyncio task inside the API process (one container for M1); `false` plus
  `python -m app.worker` runs it standalone (separate container in prod).
- Blocking work (ffmpeg, model calls, DB) runs via `run_in_executor` so the API
  event loop stays responsive in in-process mode.

## Consequences

- Positive: zero extra infrastructure; the queue survives restarts; the same
  code path is tested locally and in CI (SQLite) and runs in prod (Postgres).
- Positive: full audit trail — `job_events` records every stage transition, and
  the UI renders it as the pipeline log.
- Negative: polling adds ≤ `worker_poll_seconds` latency (0.5 s default — fine
  next to a transcription that takes minutes).
- Negative: SQLite allows only one writer; that is acceptable at
  `worker_max_concurrent_jobs=1`. **Migration trigger:** when we need >2
  concurrent workers or multiple hosts, move to Postgres (same code) or adopt
  Celery/arq with Redis — the worker boundary is a single function
  (`process_job(db, job_id)`), so swapping the transport touches nothing else.

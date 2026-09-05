"""Shared pytest fixtures.

IMPORTANT: the DB engine and upload dir are bound at import time, so the test
environment must be configured *before* `app.*` is imported. That is what the
module-level block below does — do not move it into a fixture.

Tests run against the mock providers by default: fully offline, deterministic,
zero cost. Cloud adapters are tested separately with mocked HTTP transports.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="echoneura-tests-"))
os.environ.setdefault("ECHONEURA_ENVIRONMENT", "development")
os.environ["ECHONEURA_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["ECHONEURA_UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["ECHONEURA_VOICE_DIR"] = str(_TMP / "voice")
os.environ["ECHONEURA_ASR_PROVIDER"] = "mock"
os.environ["ECHONEURA_DIARIZATION_PROVIDER"] = "none"
os.environ["ECHONEURA_ENRICH_PROVIDER"] = "mock"
os.environ["ECHONEURA_WORKER_IN_PROCESS"] = "false"
os.environ["ECHONEURA_CORS_ORIGINS"] = "*"
for key in (
    "ASSEMBLYAI_API_KEY",
    "DEEPGRAM_API_KEY",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "HUGGINGFACE_TOKEN",
):
    os.environ.pop(f"ECHONEURA_{key}", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.audio import synth_sample_wav  # noqa: E402


def pytest_sessionfinish(session, exitstatus) -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _database() -> Generator[None, None, None]:
    init_db()
    yield


@pytest.fixture(autouse=True)
def _clean_tables(db) -> Generator[None, None, None]:
    """Isolate tests: the DB file is session-scoped, so wipe rows per test.

    Cheaper than rebuilding an engine per test, and it keeps `run_one()`
    (which opens its own session from SessionLocal) seeing the same data.
    """
    from sqlalchemy import delete

    from app.models import Job, JobEvent, Segment, Speaker, Transcript

    for model in (JobEvent, Segment, Speaker, Transcript, Job):
        db.execute(delete(model))
    db.commit()
    yield


@pytest.fixture(autouse=True)
def _clear_provider_caches() -> Generator[None, None, None]:
    """Provider instances are lru_cached on settings.

    Tests monkeypatch settings to exercise other providers; without clearing the
    cache a patched provider leaks into the next test.
    """
    from app.providers.registry import (
        get_asr_provider,
        get_diarization_provider,
        get_enrichment_provider,
    )

    for fn in (get_asr_provider, get_diarization_provider, get_enrichment_provider):
        fn.cache_clear()
    yield
    for fn in (get_asr_provider, get_diarization_provider, get_enrichment_provider):
        fn.cache_clear()


@pytest.fixture
def db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """TestClient with lifespan, but ECHONEURA_WORKER_IN_PROCESS=false above means
    no background worker starts. Tests that need processing drive the worker
    explicitly with `app.worker.run_one`, which keeps timing deterministic.
    """
    with TestClient(app) as c:
        yield c


@pytest.fixture
def worker_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """TestClient with the in-process worker running, for true end-to-end tests."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "worker_in_process", True)
    monkeypatch.setattr(settings, "worker_poll_seconds", 0.05)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_wav() -> Path:
    path = _TMP / "sample.wav"
    synth_sample_wav(path, seconds=12.0)
    return path


@pytest.fixture
def long_sample_wav() -> Path:
    path = _TMP / "sample_long.wav"
    synth_sample_wav(path, seconds=90.0)
    return path


@pytest.fixture
def not_audio_file() -> Path:
    path = _TMP / "notes.txt"
    path.write_text("this is not audio\n", encoding="utf-8")
    return path


def upload(client: TestClient, path: Path, *, language: str = "auto", **kwargs):
    """POST a file to /api/jobs the way a browser would."""
    with path.open("rb") as fh:
        return client.post(
            "/api/jobs",
            files={"file": (path.name, fh, kwargs.pop("content_type", "audio/wav"))},
            data={"language": language, "word_timestamps": "true"},
            **kwargs,
        )

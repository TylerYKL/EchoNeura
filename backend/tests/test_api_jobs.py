"""End-to-end API tests: upload -> queue -> process -> edit -> export.

Jobs are processed by calling `run_one` explicitly so the assertions are
deterministic; `test_end_to_end_with_in_process_worker` covers the real
background path.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import JobStatus
from app.worker import run_one
from tests.conftest import upload


def process(client: TestClient, db, job_id: str) -> dict:
    """Run the worker once and return the fresh job payload."""
    assert run_one(db) == job_id
    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def create_and_process(client: TestClient, db, path: Path) -> dict:
    resp = upload(client, path)
    assert resp.status_code == 201, resp.text
    return process(client, db, resp.json()["id"])


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health_reports_providers_and_ffmpeg(client: TestClient, db) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["app"] == "EchoNeura"
    assert body["providers"]["asr"] == "mock"
    assert body["ffmpeg"], "ffmpeg must resolve via imageio-ffmpeg"
    assert body["missing_config"] == []
    assert body["worker"]["mode"] == "standalone"  # worker_in_process=false in tests
    assert body["database"] == "sqlite"

    # Same payload on the platform probe path.
    assert client.get("/healthz").json()["status"] == "ok"


def test_health_flags_missing_credentials(client: TestClient, db, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "asr_provider", "assemblyai")
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert "ECHONEURA_ASSEMBLYAI_API_KEY" in body["missing_config"]
    assert body["providers_ready"]["asr"] is False


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
def test_upload_creates_a_queued_job(client: TestClient, db, sample_wav: Path) -> None:
    resp = upload(client, sample_wav)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == JobStatus.QUEUED.value
    assert body["progress"] == 5
    assert body["original_filename"] == "sample.wav"
    assert body["file_size_bytes"] == sample_wav.stat().st_size
    assert body["language"] == "auto"
    assert body["audio_duration_seconds"] is None, "duration is filled in by the worker"


def test_upload_writes_the_file_under_the_job_id(client: TestClient, db, sample_wav: Path) -> None:
    from app.core.config import settings

    job_id = upload(client, sample_wav).json()["id"]
    job_dir = settings.upload_dir / job_id
    files = list(job_dir.glob("*"))
    assert len(files) == 1
    assert files[0].stat().st_size == sample_wav.stat().st_size


def test_upload_accepts_language_and_word_timestamp_options(
    client: TestClient, db, sample_wav: Path
) -> None:
    with sample_wav.open("rb") as fh:
        resp = client.post(
            "/api/jobs",
            files={"file": ("ta-interview.mp3", fh, "audio/mpeg")},
            data={"language": "ta", "word_timestamps": "false"},
        )
    assert resp.status_code == 201
    assert resp.json()["language"] == "ta"
    assert resp.json()["original_filename"] == "ta-interview.mp3"


def test_upload_rejects_non_audio(client: TestClient, db, not_audio_file: Path) -> None:
    resp = upload(client, not_audio_file, content_type="text/plain")
    assert resp.status_code == 415
    assert "Unsupported file type" in resp.json()["detail"]
    # Nothing was persisted.
    assert client.get("/api/jobs").json()["total"] == 0


def test_upload_rejects_unsupported_language(client: TestClient, db, sample_wav: Path) -> None:
    resp = upload(client, sample_wav, language="klingon")
    assert resp.status_code == 422
    assert "Unsupported language" in resp.json()["detail"]


def test_upload_rejects_empty_file(client: TestClient, db, tmp_path: Path) -> None:
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    resp = upload(client, empty)
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_upload_sanitises_hostile_filenames(
    client: TestClient, db, sample_wav: Path, tmp_path: Path
) -> None:
    """No path traversal, no shell metacharacters in the stored name."""
    hostile = tmp_path / "safe.wav"
    hostile.write_bytes(sample_wav.read_bytes())
    with hostile.open("rb") as fh:
        resp = client.post(
            "/api/jobs",
            files={"file": ("../../etc/passwd; rm -rf /.wav", fh, "audio/wav")},
            data={"language": "auto"},
        )
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    from app.core.config import settings

    files = list((settings.upload_dir / job_id).iterdir())
    assert len(files) == 1
    assert files[0].parent == settings.upload_dir / job_id
    assert ".." not in files[0].name
    assert "/" not in files[0].name


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
def test_list_jobs_pagination_and_filter(client: TestClient, db, sample_wav: Path) -> None:
    ids = [upload(client, sample_wav).json()["id"] for _ in range(3)]
    run_one(db)

    body = client.get("/api/jobs").json()
    assert body["total"] == 3
    assert len(body["items"]) == 3

    page = client.get("/api/jobs", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 1

    done = client.get("/api/jobs", params={"status": "completed"}).json()
    assert done["total"] == 1
    assert done["items"][0]["id"] in ids
    assert client.get("/api/jobs", params={"status": "failed"}).json()["total"] == 0


def test_list_jobs_rejects_bad_limit(client: TestClient, db) -> None:
    assert client.get("/api/jobs", params={"limit": 0}).status_code == 422


def test_get_missing_job_404s(client: TestClient, db) -> None:
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_job_detail_has_no_transcript_until_processed(
    client: TestClient, db, sample_wav: Path
) -> None:
    job_id = upload(client, sample_wav).json()["id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["transcript"] is None
    assert body["events"], "the audit trail starts at creation"
    assert body["events"][0]["stage"] == "queued"


def test_job_detail_after_processing(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    assert job["status"] == "completed"
    assert job["progress"] == 100

    transcript = job["transcript"]
    assert transcript is not None
    assert transcript["full_text"]
    assert transcript["revision"] == 1
    assert len(transcript["segments"]) > 3
    assert len(transcript["speakers"]) == 2
    assert transcript["speakers"][0]["display_name"] == "Speaker 1"
    assert transcript["speakers"][0]["color"].startswith("#")
    assert all(s["segment_count"] >= 0 for s in transcript["speakers"])
    assert sum(s["word_count"] for s in transcript["speakers"]) > 0

    first = transcript["segments"][0]
    assert first["text"]
    assert first["end"] >= first["start"]
    assert first["speaker_display_name"]
    assert first["words"], "word timestamps are an M1 requirement"
    assert {"w", "s", "e"} <= set(first["words"][0])

    stages = [e["stage"] for e in job["events"]]
    for expected in ("queued", "preprocessing", "transcribing", "merging", "aligning", "completed"):
        assert expected in stages


def test_audio_endpoint_streams_the_upload(client: TestClient, db, sample_wav: Path) -> None:
    job_id = upload(client, sample_wav).json()["id"]
    resp = client.get(f"/api/jobs/{job_id}/audio")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert len(resp.content) == sample_wav.stat().st_size

    # Range requests are what makes the <audio> scrubber work.
    ranged = client.get(f"/api/jobs/{job_id}/audio", headers={"Range": "bytes=0-99"})
    assert ranged.status_code in (200, 206)


def test_audio_404s_for_unknown_job(client: TestClient, db) -> None:
    assert client.get("/api/jobs/nope/audio").status_code == 404


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def test_srt_export_matches_the_ui_shape(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    resp = client.get(f"/api/jobs/{job['id']}/export/srt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-subrip")
    assert "content-disposition" in resp.headers
    assert "sample_long.srt" in resp.headers["content-disposition"]

    body = resp.text
    blocks = [b for b in body.split("\n\n") if b.strip()]
    assert len(blocks) >= 3
    assert blocks[0].split("\n")[0] == "1"
    assert "-->" in blocks[0].split("\n")[1]
    assert "Speaker 1:" in body or "Speaker 2:" in body
    # Timestamps must be SRT-formatted with a comma.
    assert re.search(r"\d{2}:\d{2}:\d{2},\d{3}", body), body[:200]


def test_srt_export_without_speaker_names(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    body = client.get(
        f"/api/jobs/{job['id']}/export/srt", params={"include_speaker_names": "false"}
    ).text
    assert "Speaker 1:" not in body
    assert "Speaker 2:" not in body


def test_all_export_formats(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    exports = {
        "srt": "application/x-subrip",
        "vtt": "text/vtt",
        "txt": "text/plain",
        "md": "text/markdown",
        "json": "application/json",
    }
    for fmt, media in exports.items():
        resp = client.get(f"/api/jobs/{job['id']}/export/{fmt}")
        assert resp.status_code == 200, fmt
        assert resp.headers["content-type"].startswith(media), fmt
        assert resp.text.strip(), fmt
        assert f".{fmt}" in resp.headers["content-disposition"]

    vtt = client.get(f"/api/jobs/{job['id']}/export/vtt").text
    assert vtt.startswith("WEBVTT")

    txt = client.get(f"/api/jobs/{job['id']}/export/txt").text
    assert txt.startswith("[0:00] Speaker 1:")

    md = client.get(f"/api/jobs/{job['id']}/export/md").text
    assert md.startswith("# sample_long")
    assert "**Speaker 1**" in md

    payload = json.loads(client.get(f"/api/jobs/{job['id']}/export/json").text)
    assert payload["job"]["id"] == job["id"]
    assert payload["transcript"]["segments"]
    assert payload["transcript"]["speakers"]


def test_export_line_width_options(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    narrow = client.get(
        f"/api/jobs/{job['id']}/export/srt",
        params={"max_chars_per_line": 15, "max_lines_per_cue": 1},
    ).text
    for line in narrow.split("\n"):
        if "-->" in line or line.strip().isdigit() or not line.strip():
            continue
        assert len(line) <= 15, line


def test_transcript_txt_inline_view(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    resp = client.get(f"/api/jobs/{job['id']}/transcript.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "Speaker 1:" in resp.text


def test_export_before_processing_is_a_409(client: TestClient, db, sample_wav: Path) -> None:
    job_id = upload(client, sample_wav).json()["id"]
    resp = client.get(f"/api/jobs/{job_id}/export/srt")
    assert resp.status_code == 409
    assert "no transcript" in resp.json()["detail"]


def test_export_unknown_format_is_a_422(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    assert client.get(f"/api/jobs/{job['id']}/export/docx").status_code == 422


# --------------------------------------------------------------------------- #
# Editing
# --------------------------------------------------------------------------- #
def test_rename_speaker_updates_exports(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    speaker = job["transcript"]["speakers"][0]

    resp = client.patch(
        f"/api/jobs/{job['id']}/speakers/{speaker['id']}", json={"display_name": "Dr. Chen"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Dr. Chen"
    assert resp.json()["is_edited"] is True

    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert "Dr. Chen" in {s["display_name"] for s in detail["transcript"]["speakers"]}
    assert any(
        seg["speaker_display_name"] == "Dr. Chen" for seg in detail["transcript"]["segments"]
    )
    assert "Dr. Chen:" in client.get(f"/api/jobs/{job['id']}/export/srt").text


def test_rename_speaker_trims_and_validates(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    speaker_id = job["transcript"]["speakers"][0]["id"]
    url = f"/api/jobs/{job['id']}/speakers/{speaker_id}"

    assert client.patch(url, json={"display_name": "  Padded  "}).json()["display_name"] == "Padded"
    assert client.patch(url, json={"display_name": "   "}).status_code == 422
    assert client.patch(url, json={"display_name": ""}).status_code == 422
    assert client.patch(url, json={"display_name": "x" * 129}).status_code == 422


def test_rename_unknown_speaker_404s(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    resp = client.patch(f"/api/jobs/{job['id']}/speakers/nope", json={"display_name": "X"})
    assert resp.status_code == 404


def test_recolor_speaker(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    speaker_id = job["transcript"]["speakers"][0]["id"]
    resp = client.patch(
        f"/api/jobs/{job['id']}/speakers/{speaker_id}/color", params={"color": "#FF8800"}
    )
    assert resp.status_code == 200
    assert resp.json()["color"] == "#ff8800"
    assert (
        client.patch(
            f"/api/jobs/{job['id']}/speakers/{speaker_id}/color", params={"color": "red"}
        ).status_code
        == 422
    )


def test_edit_segment_text(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    segment = job["transcript"]["segments"][0]

    resp = client.patch(
        f"/api/jobs/{job['id']}/segments/{segment['id']}", json={"text": "Corrected line."}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "Corrected line."
    assert resp.json()["is_edited"] is True
    assert resp.json()["original_text"] == segment["text"], "provenance must be preserved"

    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["transcript"]["revision"] == 2
    assert "Corrected line." in detail["transcript"]["full_text"]
    assert "Corrected line." in client.get(f"/api/jobs/{job['id']}/export/srt").text


def test_reassign_segment_to_another_speaker(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    speakers = job["transcript"]["speakers"]
    segment = next(s for s in job["transcript"]["segments"] if s["speaker_id"] == speakers[0]["id"])
    other = speakers[1]

    resp = client.patch(
        f"/api/jobs/{job['id']}/segments/{segment['id']}", json={"speaker_id": other["id"]}
    )
    assert resp.status_code == 200
    assert resp.json()["speaker_id"] == other["id"]
    assert resp.json()["speaker_display_name"] == other["display_name"]

    detail = client.get(f"/api/jobs/{job['id']}").json()
    counts = {s["id"]: s["word_count"] for s in detail["transcript"]["speakers"]}
    assert counts[other["id"]] > 0


def test_reassign_to_foreign_speaker_is_rejected(
    client: TestClient, db, long_sample_wav: Path
) -> None:
    job_a = create_and_process(client, db, long_sample_wav)
    job_b = create_and_process(client, db, long_sample_wav)
    seg_a = job_a["transcript"]["segments"][0]
    speaker_b = job_b["transcript"]["speakers"][0]

    resp = client.patch(
        f"/api/jobs/{job_a['id']}/segments/{seg_a['id']}", json={"speaker_id": speaker_b["id"]}
    )
    assert resp.status_code == 422


def test_edit_segment_timings_and_validation(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    segment = job["transcript"]["segments"][0]
    url = f"/api/jobs/{job['id']}/segments/{segment['id']}"

    ok = client.patch(url, json={"start": 1.5, "end": 4.25})
    assert ok.status_code == 200
    assert ok.json()["start"] == 1.5
    assert ok.json()["end"] == 4.25

    assert client.patch(url, json={"start": -1}).status_code == 422
    bad = client.patch(url, json={"end": 0.1})
    assert bad.status_code == 422
    assert "before start" in bad.json()["detail"]


def test_bulk_edit_segments(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    segments = job["transcript"]["segments"][:3]
    payload = {
        "updates": [
            {"id": segments[0]["id"], "text": "One."},
            {"id": segments[1]["id"], "text": "Two."},
            {"id": segments[2]["id"], "text": "Three."},
        ]
    }
    resp = client.patch(f"/api/jobs/{job['id']}/segments", json=payload)
    assert resp.status_code == 200, resp.text
    assert [s["text"] for s in resp.json()] == ["One.", "Two.", "Three."]

    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["transcript"]["revision"] == 2
    txt = client.get(f"/api/jobs/{job['id']}/export/txt").text
    assert "One." in txt and "Three." in txt


def test_bulk_edit_reports_missing_segments(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    resp = client.patch(
        f"/api/jobs/{job['id']}/segments",
        json={"updates": [{"id": "ghost", "text": "x"}]},
    )
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


def test_bulk_edit_with_no_updates_is_a_noop(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    resp = client.patch(f"/api/jobs/{job['id']}/segments", json={"updates": []})
    assert resp.status_code == 200
    assert resp.json() == []


def test_reset_edits_restores_model_output(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    segment = job["transcript"]["segments"][0]
    client.patch(f"/api/jobs/{job['id']}/segments/{segment['id']}", json={"text": "My edit."})

    resp = client.post(f"/api/jobs/{job['id']}/segments/reset")
    assert resp.status_code == 200
    restored = next(s for s in resp.json()["transcript"]["segments"] if s["id"] == segment["id"])
    assert restored["text"] == segment["text"]
    assert restored["is_edited"] is False
    # Speaker renames are kept — they are a separate concern from text edits.
    assert resp.json()["transcript"]["speakers"][0]["display_name"] == "Speaker 1"


def test_editing_before_processing_is_a_409(client: TestClient, db, sample_wav: Path) -> None:
    job_id = upload(client, sample_wav).json()["id"]
    resp = client.patch(f"/api/jobs/{job_id}/segments/whatever", json={"text": "x"})
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def test_cancel_a_queued_job(client: TestClient, db, sample_wav: Path) -> None:
    job_id = upload(client, sample_wav).json()["id"]
    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    # A cancelled job must not be picked up by the worker.
    assert run_one(db) is None


def test_cancel_a_completed_job_is_a_409(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_reprocess_clears_the_transcript(client: TestClient, db, long_sample_wav: Path) -> None:
    job = create_and_process(client, db, long_sample_wav)
    resp = client.post(f"/api/jobs/{job['id']}/reprocess")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert resp.json()["attempts"] == 0

    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["transcript"] is None

    again = process(client, db, job["id"])
    assert again["status"] == "completed"
    assert again["transcript"]["segments"], "reprocessing must rebuild the transcript"


def test_delete_job_removes_the_file(client: TestClient, db, sample_wav: Path) -> None:
    from app.core.config import settings

    job_id = upload(client, sample_wav).json()["id"]
    assert (settings.upload_dir / job_id).exists()

    resp = client.delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert not (settings.upload_dir / job_id).exists()
    assert client.get("/api/jobs").json()["total"] == 0


def test_delete_unknown_job_404s(client: TestClient, db) -> None:
    assert client.delete("/api/jobs/ghost").status_code == 404


# --------------------------------------------------------------------------- #
# True end-to-end with the in-process worker
# --------------------------------------------------------------------------- #
def test_end_to_end_with_in_process_worker(worker_client: TestClient, sample_wav: Path) -> None:
    """Upload -> background worker picks it up -> completed, with no manual run."""
    job_id = upload(worker_client, sample_wav).json()["id"]

    deadline = time.monotonic() + 30
    body: dict = {}
    while time.monotonic() < deadline:
        body = worker_client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"completed", "failed"}:
            break
        time.sleep(0.2)

    assert body["status"] == "completed", body.get("error") or body
    assert body["transcript"]["segments"]
    assert worker_client.get(f"/api/jobs/{job_id}/export/srt").status_code == 200


@pytest.mark.parametrize("language", ["auto", "en", "ta", "es"])
def test_language_option_flows_through_the_pipeline(
    client: TestClient, db, sample_wav: Path, language: str
) -> None:
    job_id = upload(client, sample_wav, language=language).json()["id"]
    job = process(client, db, job_id)
    assert job["language"] == language
    assert job["status"] == "completed"
    if language != "auto":
        assert job["transcript"]["language"] == language

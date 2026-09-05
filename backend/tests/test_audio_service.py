"""Audio probing/conversion via the static ffmpeg build.

These tests need ffmpeg; they skip cleanly rather than fail on a machine where
neither a system ffmpeg nor imageio-ffmpeg is installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.audio import (
    AUDIO_EXTENSIONS,
    AudioError,
    convert_to_model_input,
    ffmpeg_exe,
    ffmpeg_version,
    looks_like_audio,
    probe,
    synth_sample_wav,
)

pytestmark = pytest.mark.skipif(
    ffmpeg_version() is None, reason="ffmpeg unavailable (install imageio-ffmpeg)"
)


def test_ffmpeg_resolves() -> None:
    path = ffmpeg_exe()
    assert path and Path(path).exists()
    assert "ffmpeg" in ffmpeg_version().lower()


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return synth_sample_wav(tmp_path / "probe-me.wav", seconds=6.5)


def test_probe_reports_real_duration(wav: Path) -> None:
    info = probe(wav)
    assert info.duration_seconds == pytest.approx(6.5, abs=0.05)
    assert info.sample_rate == 16_000
    assert info.channels == 1
    assert info.codec
    assert info.bit_rate and info.bit_rate > 0


def test_probe_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="not found"):
        probe(tmp_path / "nope.wav")


def test_probe_rejects_non_audio(tmp_path: Path, not_audio_file: Path) -> None:
    """A text file must fail loudly, not silently produce a 0s job."""
    with pytest.raises(AudioError):
        probe(not_audio_file)


def test_convert_to_16k_mono(wav: Path, tmp_path: Path) -> None:
    out = convert_to_model_input(wav, tmp_path / "out.wav")
    assert out.exists()
    info = probe(out)
    assert info.sample_rate == 16_000
    assert info.channels == 1
    assert info.duration_seconds == pytest.approx(6.5, abs=0.1)


def test_convert_downsamples_a_high_rate_file(tmp_path: Path) -> None:
    src = tmp_path / "hi.wav"
    synth_sample_wav(src, seconds=2.0, sample_rate=44_100)
    assert probe(src).sample_rate == 44_100
    out = convert_to_model_input(src, tmp_path / "lo.wav")
    assert probe(out).sample_rate == 16_000


def test_convert_creates_missing_parent_dirs(wav: Path, tmp_path: Path) -> None:
    out = convert_to_model_input(wav, tmp_path / "deep" / "nested" / "out.wav")
    assert out.exists()


def test_convert_failure_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.wav"
    bogus.write_bytes(b"not audio at all")
    with pytest.raises(AudioError, match="ffmpeg"):
        convert_to_model_input(bogus, tmp_path / "out.wav")


def test_synth_wav_is_valid_pcm(tmp_path: Path) -> None:
    import wave

    path = synth_sample_wav(tmp_path / "s.wav", seconds=1.0)
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16_000
        assert w.getnframes() == 16_000
        assert w.readframes(100)


def test_synth_is_deterministic(tmp_path: Path) -> None:
    a = synth_sample_wav(tmp_path / "a.wav", seconds=1.0).read_bytes()
    b = synth_sample_wav(tmp_path / "b.wav", seconds=1.0).read_bytes()
    assert a == b


# --------------------------------------------------------------------------- #
# Type sniffing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name", ["a.mp3", "b.WAV", "c.m4a", "d.flac", "e.ogg", "f.opus", "g.webm", "h.mp4", "i.mov"]
)
def test_recognises_audio_and_video_extensions(name: str) -> None:
    assert looks_like_audio(name)


def test_extension_matching_is_case_insensitive() -> None:
    assert looks_like_audio("INTERVIEW.MP3")
    assert looks_like_audio("talk.M4A")


def test_recognises_by_mime_type() -> None:
    assert looks_like_audio("recording", "audio/mpeg")
    assert looks_like_audio("clip", "video/mp4")
    assert looks_like_audio("weird.bin", "audio/x-m4a")


def test_rejects_non_audio() -> None:
    assert not looks_like_audio("notes.txt")
    assert not looks_like_audio("notes.txt", "text/plain")
    assert not looks_like_audio("archive.zip", "application/zip")
    assert not looks_like_audio("no-extension")


def test_rejects_spoofed_extension_with_text_mime() -> None:
    """A .mp3 name with text/plain content is still worth accepting at the API
    layer (browsers often guess wrong); probe() is the real gatekeeper."""
    assert looks_like_audio("really.mp3", "text/plain")
    assert ".mp3" in AUDIO_EXTENSIONS

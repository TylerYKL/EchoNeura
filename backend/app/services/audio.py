"""Audio helpers built on a static ffmpeg binary.

Why `imageio-ffmpeg`?
    apt/ffmpeg is unavailable in restricted environments (no root, no package
    lists). `imageio_ffmpeg` ships a self-contained static ffmpeg 7.x binary via
    pip, so probe/convert works identically on a laptop, in CI, and in a slim
    Docker image. If a system ffmpeg exists we prefer it (faster to spawn, has
    ffprobe).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS: set[str] = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".webm",
    ".mp4",
    ".m4v",
    ".mov",
    ".wma",
    ".aiff",
    ".aif",
    ".amr",
    ".3gp",
}

# What Whisper-class models actually want: 16 kHz mono PCM.
TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1


class AudioError(RuntimeError):
    """Raised when a file cannot be probed or converted."""


@dataclass(slots=True)
class AudioInfo:
    duration_seconds: float
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None
    bit_rate: int | None = None
    container: str | None = None
    raw: dict | None = None


_ffmpeg_path_cache: str | None = None


def ffmpeg_exe() -> str:
    """Resolve an ffmpeg binary: system first, then the pip static build."""
    global _ffmpeg_path_cache
    if _ffmpeg_path_cache:
        return _ffmpeg_path_cache

    system = shutil.which("ffmpeg")
    if system:
        _ffmpeg_path_cache = system
        return system

    try:
        import imageio_ffmpeg

        _ffmpeg_path_cache = imageio_ffmpeg.get_ffmpeg_exe()
        return _ffmpeg_path_cache
    except Exception as exc:  # pragma: no cover - environment dependent
        raise AudioError(
            "No ffmpeg available. Install a system ffmpeg or `pip install imageio-ffmpeg`."
        ) from exc


def ffmpeg_version() -> str | None:
    try:
        out = subprocess.run([ffmpeg_exe(), "-version"], capture_output=True, text=True, timeout=30)
        return out.stdout.splitlines()[0] if out.stdout else None
    except Exception:  # noqa: BLE001 - a missing/broken ffmpeg must not kill /health
        return None


def looks_like_audio(filename: str, mime_type: str | None = None) -> bool:
    if Path(filename).suffix.lower() in AUDIO_EXTENSIONS:
        return True
    return bool(mime_type) and (
        mime_type.startswith("audio/") or mime_type.startswith("video/")  # type: ignore[union-attr]
    )


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_STREAM_AUDIO_RE = re.compile(
    r"Stream #\d+(?::\d+)?.*?Audio:\s*(?P<codec>[^,]+).*?(?P<rate>\d+)\s*Hz,\s*(?P<layout>[^,]+)"
)
_BITRATE_RE = re.compile(r"bitrate:\s*(\d+)\s*kb/s")


def _channels_from_layout(layout: str) -> int | None:
    layout = layout.strip().lower()
    if layout == "mono":
        return 1
    if layout == "stereo":
        return 2
    m = re.search(r"(\d+)\s*channels", layout)
    return int(m.group(1)) if m else None


def probe(path: str | Path) -> AudioInfo:
    """Return duration/metadata for an audio file.

    Uses `ffprobe -show_format -show_streams` when available and falls back to
    parsing `ffmpeg -i` stderr (the static imageio build has no ffprobe).
    """
    path = Path(path)
    if not path.exists():
        raise AudioError(f"File not found: {path}")

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                raise AudioError(f"Unparseable ffprobe output for {path.name}") from exc
            fmt = data.get("format", {})
            streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
            duration = fmt.get("duration") or (streams[0].get("duration") if streams else None)
            if duration is None:
                raise AudioError(f"ffprobe reported no duration for {path.name}")
            first = streams[0] if streams else {}
            return AudioInfo(
                duration_seconds=float(duration),
                sample_rate=int(first["sample_rate"]) if first.get("sample_rate") else None,
                channels=int(first["channels"]) if first.get("channels") else None,
                codec=first.get("codec_name"),
                bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
                container=fmt.get("format_name"),
                raw=data,
            )

    # Fallback: parse `ffmpeg -i` stderr.
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    stderr = proc.stderr or ""
    m = _DURATION_RE.search(stderr)
    if not m:
        raise AudioError(f"Could not determine duration for {path.name}. Is it a valid audio file?")
    hours, minutes, seconds = int(m.group(1)), int(m.group(2)), float(m.group(3))
    duration = hours * 3600 + minutes * 60 + seconds

    sample_rate = channels = None
    codec = None
    sm = _STREAM_AUDIO_RE.search(stderr)
    if sm:
        codec = sm.group("codec").strip()
        sample_rate = int(sm.group("rate"))
        channels = _channels_from_layout(sm.group("layout"))

    bit_rate = None
    bm = _BITRATE_RE.search(stderr)
    if bm:
        bit_rate = int(bm.group(1)) * 1000

    return AudioInfo(
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
        codec=codec,
        bit_rate=bit_rate,
        container=None,
        raw={"stderr_head": stderr[:2000]},
    )


def convert_to_model_input(
    src: str | Path,
    dst: str | Path,
    *,
    sample_rate: int = TARGET_SAMPLE_RATE,
    channels: int = TARGET_CHANNELS,
) -> Path:
    """Normalise audio to mono 16 kHz WAV — what every ASR provider wants.

    Doing this once up front keeps provider adapters trivial and makes local
    faster-whisper runs dramatically cheaper (no resampling per chunk).
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",  # strip any video stream
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0 or not dst.exists():
        raise AudioError(f"ffmpeg conversion failed: {(proc.stderr or '').strip()[:800]}")
    return dst


def synth_sample_wav(
    dst: str | Path,
    *,
    seconds: float = 12.0,
    sample_rate: int = TARGET_SAMPLE_RATE,
) -> Path:
    """Generate a small deterministic WAV so the app is testable with no assets.

    Produces alternating tones (one per mock 'speaker') — enough for ffmpeg,
    duration probing and the full pipeline to exercise real code paths.
    Pure stdlib `wave`, no numpy required.
    """
    import math

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(seconds * sample_rate)
    # Two "voices": 220 Hz and 330 Hz, alternating every 3 s.
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        chunk = 2048
        written = 0
        while written < n_frames:
            n = min(chunk, n_frames - written)
            frames = bytearray()
            for i in range(n):
                t = (written + i) / sample_rate
                freq = 220.0 if int(t // 3) % 2 == 0 else 330.0
                # gentle attack/decay to avoid clicks
                env = min(1.0, (t % 3) * 8) * min(1.0, (3 - (t % 3)) * 8)
                sample = int(12000 * env * math.sin(2 * math.pi * freq * t))
                frames += sample.to_bytes(2, "little", signed=True)
            w.writeframes(bytes(frames))
            written += n
    return dst

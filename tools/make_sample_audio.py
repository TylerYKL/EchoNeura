#!/usr/bin/env python3
"""Generate a demo WAV so you can exercise the app without sourcing audio.

    make sample
    # or:  cd backend && PYTHONPATH=. ../backend/.venv/bin/python ../tools/make_sample_audio.py

Writes data/samples/demo.wav (30 s, mono 16 kHz) — two alternating tones that
stand in for two speakers. The mock ASR provider derives a deterministic
transcript from the real duration, so the whole pipeline (probe -> transcribe ->
diarize -> merge -> persist -> export) runs end to end.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.audio import probe, synth_sample_wav  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=30.0, help="clip length (default 30)")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "data" / "samples" / "demo.wav",
        help="output path",
    )
    args = parser.parse_args()

    path = synth_sample_wav(args.out, seconds=args.seconds)
    info = probe(path)
    print(f"✓ wrote {path}")
    print(
        f"  {info.duration_seconds:.2f}s · {info.sample_rate} Hz · "
        f"{info.channels} ch · {path.stat().st_size / 1024:.0f} KB"
    )
    print("  Upload it at http://localhost:3000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

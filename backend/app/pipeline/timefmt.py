"""Time formatting shared by SRT/VTT/TXT exports.

Split out (and unit-tested) because off-by-one-millisecond subtitle timing is the
classic bug in this kind of tool, and because SRT and WebVTT differ in exactly
one character (',' vs '.').
"""

from __future__ import annotations

import math


def clamp_non_negative(seconds: float) -> float:
    if seconds is None or math.isnan(seconds):
        return 0.0
    return max(0.0, float(seconds))


def format_timestamp(seconds: float, *, decimal_separator: str = ",", millis: bool = True) -> str:
    """Format seconds as HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT).

    Floors to milliseconds instead of rounding, so a cue never starts before its
    audio and consecutive cues never overlap by 1ms.
    """
    seconds = clamp_non_negative(seconds)
    total_ms = math.floor(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    base = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{base}{decimal_separator}{ms:03d}" if millis else base


def format_srt_timestamp(seconds: float) -> str:
    return format_timestamp(seconds, decimal_separator=",")


def format_vtt_timestamp(seconds: float) -> str:
    return format_timestamp(seconds, decimal_separator=".")


def format_clock(seconds: float, *, show_hours: bool | None = None) -> str:
    """Compact human clock for the UI: 3:07 or 1:02:03."""
    seconds = clamp_non_negative(seconds)
    total = math.floor(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours or show_hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_duration_human(seconds: float | None) -> str:
    if not seconds:
        return "0s"
    seconds = clamp_non_negative(seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    return format_clock(seconds)

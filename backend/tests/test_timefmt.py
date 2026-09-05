"""Timestamp formatting — the classic source of off-by-one subtitle bugs."""

from __future__ import annotations

import pytest

from app.pipeline.timefmt import (
    clamp_non_negative,
    format_clock,
    format_duration_human,
    format_srt_timestamp,
    format_timestamp,
    format_vtt_timestamp,
)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00,000"),
        (0.001, "00:00:00,001"),
        (1.5, "00:00:01,500"),
        (59.999, "00:00:59,999"),
        (60, "00:01:00,000"),
        (61.234, "00:01:01,234"),
        (3599.9999, "00:59:59,999"),
        (3600, "01:00:00,000"),
        (3661.5, "01:01:01,500"),
        (7325.123, "02:02:05,123"),
    ],
)
def test_srt_timestamps(seconds: float, expected: str) -> None:
    assert format_srt_timestamp(seconds) == expected


def test_vtt_uses_dot_separator() -> None:
    assert format_vtt_timestamp(1.5) == "00:00:01.500"
    assert format_timestamp(1.5, decimal_separator=".") == "00:00:01.500"


def test_no_millis_option() -> None:
    assert format_timestamp(3661.5, millis=False) == "01:01:01"


def test_floors_instead_of_rounding() -> None:
    """Rounding up would make a cue start before its audio.

    1.9999s must floor to 1.999, never round to 2.000.
    """
    assert format_srt_timestamp(1.9999) == "00:00:01,999"
    assert format_srt_timestamp(0.99999) == "00:00:00,999"


def test_negative_and_nan_are_clamped() -> None:
    assert format_srt_timestamp(-5) == "00:00:00,000"
    assert format_srt_timestamp(float("nan")) == "00:00:00,000"
    assert clamp_non_negative(-1.0) == 0.0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0:00"), (7, "0:07"), (65, "1:05"), (599, "9:59"), (3600, "1:00:00"), (3725, "1:02:05")],
)
def test_clock(seconds: float, expected: str) -> None:
    assert format_clock(seconds) == expected


def test_clock_can_force_hours() -> None:
    assert format_clock(65, show_hours=True) == "0:01:05"


def test_duration_human() -> None:
    assert format_duration_human(None) == "0s"
    assert format_duration_human(12.34) == "12.3s"
    assert format_duration_human(125) == "2:05"


def test_hours_over_99_stay_correct() -> None:
    # Long-form recordings (audiobooks, lectures) exceed 99h only rarely, but
    # the formatter must not wrap or truncate.
    assert format_srt_timestamp(100 * 3600 + 5) == "100:00:05,000"

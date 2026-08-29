"""Polling-interval handling, kept free of Home Assistant imports.

Lives apart from ``__init__.py`` so the validation can be unit-tested without a
Home Assistant install, alongside the HTML parsing tests.
"""
from __future__ import annotations

import logging
import math

from .const import (
    DEFAULT_ASSUMED_FRAME_BYTES,
    DEFAULT_SCAN_INTERVAL,
    MAX_FRAME_BYTES,
    MAX_SCAN_INTERVAL,
    MIN_FRAME_BYTES,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def clamp_scan_interval(raw: object) -> int:
    """Coerce a stored polling interval to a usable number of seconds.

    Values arrive from a config entry, so they may be a float from the UI
    number selector, a string from hand-edited YAML or storage, or missing
    entirely. Anything unusable falls back to the default rather than raising,
    because a bad stored value must not stop the integration loading.

    The result is clamped to ``MIN_SCAN_INTERVAL``..``MAX_SCAN_INTERVAL``. The
    lower bound matters: these switches run a small embedded HTTP server that
    drops requests when polled too aggressively, and their web UI is known to
    hang under sustained load.
    """
    if raw is None:
        return DEFAULT_SCAN_INTERVAL
    try:
        as_float = float(raw)  # tolerate 30.0 and "30"
    except (TypeError, ValueError):
        _LOGGER.warning(
            "Invalid scan_interval %r, falling back to %s s",
            raw, DEFAULT_SCAN_INTERVAL,
        )
        return DEFAULT_SCAN_INTERVAL

    # NaN and infinity survive float() but cannot be converted to int.
    if not math.isfinite(as_float):
        _LOGGER.warning(
            "Non-finite scan_interval %r, falling back to %s s",
            raw, DEFAULT_SCAN_INTERVAL,
        )
        return DEFAULT_SCAN_INTERVAL

    seconds = int(as_float)
    return max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, seconds))


def clamp_assumed_frame_bytes(raw: object) -> int:
    """Coerce the assumed average frame size, in bytes.

    ``0`` means the estimated-throughput sensors are disabled, which is the
    default. Any other value is clamped into the legal Ethernet frame range.
    Unusable input disables the feature rather than falling back to a made-up
    size: an estimate is only meaningful when the user chose its assumption.
    """
    if raw is None:
        return DEFAULT_ASSUMED_FRAME_BYTES
    try:
        as_float = float(raw)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "Invalid assumed_frame_bytes %r, disabling estimated throughput", raw
        )
        return DEFAULT_ASSUMED_FRAME_BYTES
    if not math.isfinite(as_float):
        _LOGGER.warning(
            "Non-finite assumed_frame_bytes %r, disabling estimated throughput", raw
        )
        return DEFAULT_ASSUMED_FRAME_BYTES

    size = int(as_float)
    if size <= 0:
        return DEFAULT_ASSUMED_FRAME_BYTES
    return max(MIN_FRAME_BYTES, min(MAX_FRAME_BYTES, size))

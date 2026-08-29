"""Packet-rate derivation, kept free of Home Assistant imports.

These switches expose cumulative frame counters and nothing else — no byte or
octet counters exist on any of their pages, so a bit rate cannot be derived,
only a frame rate. See the README for why no bits/sec sensor is synthesised.

Rates are computed here from consecutive polls rather than left to a
Home Assistant derivative helper, so the integration ships a usable rate out of
the box instead of requiring one helper per port and direction.

A rate is ``None`` — never 0 — whenever it cannot be known: on the first poll,
when a counter was not read, when the elapsed time is unusable, or when a
counter went backwards because the switch cleared its statistics. Reporting 0
for "unknown" would understate a busy link and pollute averages.
"""
from __future__ import annotations

import logging
import math

from .models import PortData

_LOGGER = logging.getLogger(__name__)

# Guard against a clock that did not move (or moved backwards) between polls.
_MIN_ELAPSED = 0.5


def compute_rate(
    previous: int | None, current: int | None, elapsed: float
) -> float | None:
    """Per-second rate between two cumulative counter readings.

    Returns ``None`` when the rate is unknowable rather than guessing:

    - either reading missing (a poll that could not read the statistics page)
    - elapsed time too small or non-finite to divide by
    - the counter went backwards, meaning it was cleared between polls; the
      traffic in the interval cannot be recovered from a reset counter
    """
    if previous is None or current is None:
        return None
    if not math.isfinite(elapsed) or elapsed < _MIN_ELAPSED:
        return None
    if current < previous:
        return None
    return (current - previous) / elapsed


class RateTracker:
    """Holds the previous counter sample so rates can be derived per poll."""

    def __init__(self) -> None:
        self._timestamp: float | None = None
        # port -> (tx_packets, rx_packets)
        self._counters: dict[str, tuple[int | None, int | None]] = {}

    @property
    def has_baseline(self) -> bool:
        return self._timestamp is not None

    def update(self, ports: list[PortData], timestamp: float) -> None:
        """Set ``tx_pps``/``rx_pps`` on ``ports``, then store this sample.

        Ports are matched by number, so a port appearing or disappearing
        between polls simply has no rate for that cycle instead of being
        compared against the wrong port.
        """
        elapsed = (
            timestamp - self._timestamp if self._timestamp is not None else None
        )

        for port in ports:
            previous = self._counters.get(port.port)
            if previous is None or elapsed is None:
                port.tx_pps = None
                port.rx_pps = None
                continue

            port.tx_pps = compute_rate(previous[0], port.tx_packets, elapsed)
            port.rx_pps = compute_rate(previous[1], port.rx_packets, elapsed)

            if (
                port.tx_pps is None
                and previous[0] is not None
                and port.tx_packets is not None
                and port.tx_packets < previous[0]
            ):
                _LOGGER.debug(
                    "Port %s TX counter went backwards (%s -> %s); statistics "
                    "were cleared, so no rate for this interval",
                    port.port, previous[0], port.tx_packets,
                )

        self._timestamp = timestamp
        self._counters = {p.port: (p.tx_packets, p.rx_packets) for p in ports}

"""Data models for the TP-Link EasySmart switch integration.

Kept free of Home Assistant and aiohttp imports so the parsing layer
(``parser.py``) can be unit-tested against captured pages without a Home
Assistant install.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PortData:
    """All data for a single physical switch port.

    Counter fields are ``None`` until a statistics page has actually been read.
    ``None`` means "not read", which is distinct from a real zero — the switch
    legitimately reports 0 for a port that has never passed traffic. Publishing
    0 for an unread counter would look like a counter reset to Home Assistant's
    ``TOTAL_INCREASING`` handling and corrupt the long-term sum.
    """

    port: str                       # "1", "2", … "16"
    status: str                     # "up" | "down" | "disable"
    link: str                       # "Link Up" | "Link Down" | "Disabled"
    speed: str                      # "10M" | "100M" | "1000M" | ""
    duplex: str                     # "Full" | "Half" | ""
    admin_state: str = ""           # "Enabled" | "Disabled"
    speed_config: str = ""          # configured speed/duplex, usually "Auto"
    flow_control: str = ""          # negotiated flow control, "On" | "Off"
    flow_control_config: str = ""   # configured flow control
    trunk_group: int | None = None  # LAG id, 0 when not a member

    tx_packets: int | None = None
    rx_packets: int | None = None
    # The switch labels these TxBadPkt / RxBadPkt. On a trunk port they are
    # dominated by VLAN-filtered ingress rather than physical errors, so they
    # are deliberately NOT presented as an error condition.
    tx_bad_packets: int | None = None
    rx_bad_packets: int | None = None

    # Frames per second, derived from consecutive polls. None when unknowable:
    # first poll, an unread counter, or a counter that was cleared.
    tx_pps: float | None = None
    rx_pps: float | None = None


@dataclass
class SwitchData:
    """Full snapshot of one managed switch."""

    host: str
    name: str = ""              # descriStr — the user-set device name, not the model
    model: str = ""             # from hardwareStr, e.g. "TL-SG116E 2.20"
    mac: str = ""
    ip: str = ""
    netmask: str = ""
    gateway: str = ""
    firmware: str = ""
    port_count: int = 0
    ports: list[PortData] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    available: bool = True

    # Capability flags, resolved per scrape.
    has_bad_packet_counters: bool = False
    # Byte counters do not exist on this hardware; kept explicit so entity
    # gating reads the same way as the rest of the integration.
    has_byte_counters: bool = False

    # Assumed average frame sizes for the optional throughput estimate, in
    # bytes, per direction. 0 means no estimate is produced.
    assumed_tx_frame_bytes: int = 0
    assumed_rx_frame_bytes: int = 0

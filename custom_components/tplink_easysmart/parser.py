"""Parsing for the TP-Link EasySmart web UI ("Rpm" pages).

Everything here is a plain function over an HTML string, so it can be tested
against captured pages without Home Assistant or a live switch.

These pages render client-side: each embeds a JavaScript object literal and the
page's own script builds the table from it. So the data is extracted from the
JS source rather than from the DOM — there is no table in the HTML to parse.

``/SystemInfoRpm.htm``::

    var info_ds = {
    descriStr:[ "SW01" ],
    macStr:[ "AA:BB:CC:00:BB:02" ],
    ...
    hardwareStr:[ "TL-SG116E 2.20" ]
    };

``/PortStatisticsRpm.htm``::

    var max_port_num = 16;
    var all_info = {
    state:[1,1,...],
    link_status:[0,5,...],
    pkts:[tx_good,tx_bad,rx_good,rx_bad, ...4 per port...]
    };

``/PortSettingRpm.htm`` adds ``spd_cfg``, ``spd_act``, ``fc_cfg``, ``fc_act``
and ``trunk_info``.

Two traps these pages set, both covered by tests:

- The arrays carry **more entries than ``max_port_num``** (two extra on both
  models observed). Only the first ``max_port_num`` are real ports.
- ``descriStr`` is the **user-set device name**, not the model. The model is in
  ``hardwareStr``. Treating descriStr as the model shows "SW01" as the model.
"""
from __future__ import annotations

import logging
import re

from .const import (
    FLOW_CONTROL_MAP,
    LINK_DOWN_CODES,
    LINK_STATUS_MAP,
    PORT_STATE_MAP,
    PORT_STATUS_DISABLED,
    PORT_STATUS_DOWN,
    PORT_STATUS_UP,
)
from .models import PortData

_LOGGER = logging.getLogger(__name__)

# info_ds string fields → SwitchData attribute names.
_INFO_FIELDS: dict[str, str] = {
    "descriStr": "name",
    "hardwareStr": "model",
    "macStr": "mac",
    "ipStr": "ip",
    "netmaskStr": "netmask",
    "gatewayStr": "gateway",
    "firmwareStr": "firmware",
}


# ──────────────────────────────────────────────────────────────────────────
# Primitives
# ──────────────────────────────────────────────────────────────────────────

def parse_int_array(text: str, name: str) -> list[int] | None:
    """Extract ``name:[1,2,3]`` as a list of ints, or None when absent."""
    m = re.search(rf"\b{re.escape(name)}\s*:\s*\[([^\]]*)\]", text)
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return []
    out: list[int] = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            _LOGGER.debug("Non-integer entry %r in array %s", part, name)
            return None
    return out


def parse_string_field(text: str, name: str) -> str | None:
    """Extract ``name:[ "value" ]``, tolerating newlines inside the brackets."""
    m = re.search(
        rf"\b{re.escape(name)}\s*:\s*\[\s*\"([^\"]*)\"\s*\]", text, re.DOTALL
    )
    return m.group(1).strip() if m else None


def parse_max_port_num(text: str) -> int | None:
    """Extract ``var max_port_num = N``."""
    m = re.search(r"\bmax_port_num\s*=\s*(\d+)", text)
    return int(m.group(1)) if m else None


def split_link_status(code: int) -> tuple[str, str]:
    """Split a negotiated link code into ``(speed, duplex)``.

    ``6`` → ``("1000M", "Full")`` · ``4`` → ``("100M", "Half")``.
    A down or unknown code yields empty strings rather than a guess.
    """
    label = LINK_STATUS_MAP.get(code)
    if label is None:
        _LOGGER.debug("Unknown link status code %r", code)
        return "", ""
    m = re.fullmatch(r"(\d+)(Half|Full)", label)
    if not m:
        return "", ""      # "Link Down", "Auto", or the empty trailing entry
    return f"{m.group(1)}M", m.group(2)


# ──────────────────────────────────────────────────────────────────────────
# /SystemInfoRpm.htm
# ──────────────────────────────────────────────────────────────────────────

def parse_system_info(html: str) -> dict[str, str]:
    """Parse the ``info_ds`` block.

    Only keys the switch actually prints are returned, so a caller can tell a
    missing field from an empty one.
    """
    out: dict[str, str] = {}
    if not html:
        return out
    for js_name, attr in _INFO_FIELDS.items():
        value = parse_string_field(html, js_name)
        if value is not None:
            out[attr] = value
    return out


# ──────────────────────────────────────────────────────────────────────────
# /PortStatisticsRpm.htm
# ──────────────────────────────────────────────────────────────────────────

def parse_ports(stats_html: str) -> list[PortData]:
    """Build the port list from the statistics page.

    ``pkts`` holds four values per port in the order the switch's own script
    reads them: ``tx_good, tx_bad, rx_good, rx_bad``.
    """
    if not stats_html:
        return []

    port_count = parse_max_port_num(stats_html)
    if port_count is None:
        _LOGGER.debug("max_port_num not found; not an authenticated stats page?")
        return []

    state = parse_int_array(stats_html, "state")
    link = parse_int_array(stats_html, "link_status")
    pkts = parse_int_array(stats_html, "pkts")
    if state is None or link is None or pkts is None:
        _LOGGER.debug("Statistics arrays missing or malformed")
        return []

    # The arrays are longer than the real port count on every model observed.
    if len(state) < port_count or len(link) < port_count:
        _LOGGER.warning(
            "Statistics arrays shorter than max_port_num (%d): state=%d link=%d",
            port_count, len(state), len(link),
        )
        return []

    ports: list[PortData] = []
    for i in range(port_count):
        number = str(i + 1)
        admin = PORT_STATE_MAP.get(state[i], "")
        link_code = link[i]

        if state[i] == 0:
            port = PortData(
                port=number, status=PORT_STATUS_DISABLED, link="Disabled",
                speed="", duplex="", admin_state=admin,
            )
        elif link_code in LINK_DOWN_CODES:
            port = PortData(
                port=number, status=PORT_STATUS_DOWN, link="Link Down",
                speed="", duplex="", admin_state=admin,
            )
        else:
            speed, duplex = split_link_status(link_code)
            port = PortData(
                port=number, status=PORT_STATUS_UP, link="Link Up",
                speed=speed, duplex=duplex, admin_state=admin,
            )

        base = 4 * i
        if len(pkts) >= base + 4:
            port.tx_packets = pkts[base]
            port.tx_bad_packets = pkts[base + 1]
            port.rx_packets = pkts[base + 2]
            port.rx_bad_packets = pkts[base + 3]
        else:
            _LOGGER.debug("pkts array too short for port %s", number)

        ports.append(port)

    return ports


# ──────────────────────────────────────────────────────────────────────────
# /PortSettingRpm.htm
# ──────────────────────────────────────────────────────────────────────────

def apply_port_settings(setting_html: str, ports: list[PortData]) -> bool:
    """Overlay configured/negotiated settings onto ``ports`` in place.

    Returns True when anything was applied. This page is supplementary: a
    failure to read it must not lose the ports themselves.
    """
    if not setting_html or not ports:
        return False

    spd_cfg = parse_int_array(setting_html, "spd_cfg")
    spd_act = parse_int_array(setting_html, "spd_act")
    fc_cfg = parse_int_array(setting_html, "fc_cfg")
    fc_act = parse_int_array(setting_html, "fc_act")
    trunk = parse_int_array(setting_html, "trunk_info")

    if spd_cfg is None and fc_cfg is None:
        _LOGGER.debug("Port settings arrays not found")
        return False

    applied = False
    for i, port in enumerate(ports):
        if spd_cfg and i < len(spd_cfg):
            port.speed_config = LINK_STATUS_MAP.get(spd_cfg[i], "")
            applied = True
        if fc_cfg and i < len(fc_cfg):
            port.flow_control_config = FLOW_CONTROL_MAP.get(fc_cfg[i], "")
            applied = True
        if fc_act and i < len(fc_act):
            port.flow_control = FLOW_CONTROL_MAP.get(fc_act[i], "")
            applied = True
        if trunk and i < len(trunk):
            port.trunk_group = trunk[i]
            applied = True

        # spd_act should agree with link_status from the statistics page. When
        # it does not, the statistics page wins: it is the one the counters came
        # from, so keeping them consistent matters more than the discrepancy.
        if spd_act and i < len(spd_act):
            speed, _ = split_link_status(spd_act[i])
            if port.status == PORT_STATUS_UP and speed and speed != port.speed:
                _LOGGER.debug(
                    "Port %s speed disagrees: stats=%s settings=%s",
                    port.port, port.speed, speed,
                )

    return applied


def is_authenticated(html: str, marker: str) -> bool:
    """Whether a response is a real page rather than the login form."""
    return bool(html) and marker in html

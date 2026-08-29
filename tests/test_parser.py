"""Parser tests for the TP-Link EasySmart integration.

These run against captured web-UI pages — no switch and no Home Assistant
install required::

    python3 -m pytest tests/ -v

Fixtures are real captures from a TL-SG105E 5.0 (firmware 1.0.0 Build 20250710)
and a TL-SG116E 2.20 (firmware 1.0.0 Build 20230505), with MAC and IP addresses
replaced by documentation-range values.
"""
from __future__ import annotations

import pathlib

import pytest

from tplink_easysmart_parsing import options as options_mod  # noqa: E402
from tplink_easysmart_parsing import parser  # noqa: E402
from tplink_easysmart_parsing import rates as rates_mod  # noqa: E402
from tplink_easysmart_parsing.const import (  # noqa: E402
    AUTH_MARKER,
    DEFAULT_ASSUMED_FRAME_BYTES,
    DEFAULT_SCAN_INTERVAL,
    LINK_STATUS_MAP,
    MAX_FRAME_BYTES,
    MAX_SCAN_INTERVAL,
    MIN_FRAME_BYTES,
    MIN_SCAN_INTERVAL,
    WIRE_OVERHEAD_BYTES,
)
from tplink_easysmart_parsing.models import PortData  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def load(model: str, page: str) -> str:
    return (FIXTURES / model / page).read_text(encoding="utf-8")


@pytest.fixture
def sg105_info() -> str:
    return load("tl_sg105e", "SystemInfoRpm.htm")


@pytest.fixture
def sg105_stats() -> str:
    return load("tl_sg105e", "PortStatisticsRpm.htm")


@pytest.fixture
def sg105_setting() -> str:
    return load("tl_sg105e", "PortSettingRpm.htm")


@pytest.fixture
def sg116_info() -> str:
    return load("tl_sg116e", "SystemInfoRpm.htm")


@pytest.fixture
def sg116_stats() -> str:
    return load("tl_sg116e", "PortStatisticsRpm.htm")


@pytest.fixture
def sg116_setting() -> str:
    return load("tl_sg116e", "PortSettingRpm.htm")


# ══════════════════════════════════════════════════════════════════════════
# Primitives
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, ("", "")),          # Link Down
        (1, ("", "")),          # Auto — negotiating, no rate yet
        (2, ("10M", "Half")),
        (3, ("10M", "Full")),
        (4, ("100M", "Half")),  # absent from the base project's table
        (5, ("100M", "Full")),
        (6, ("1000M", "Full")),
        (7, ("", "")),          # trailing empty entry
        (99, ("", "")),         # unknown code is not guessed
    ],
)
def test_split_link_status(code, expected):
    assert parser.split_link_status(code) == expected


def test_link_status_map_matches_the_device_javascript():
    """The switches ship this exact array; codes 1 and 4 must be present.

    link_info = new Array("Link Down","Auto","10Half","10Full",
                          "100Half","100Full","1000Full","")
    """
    assert LINK_STATUS_MAP[1] == "Auto"
    assert LINK_STATUS_MAP[4] == "100Half"
    assert [LINK_STATUS_MAP[i] for i in range(8)] == [
        "Link Down", "Auto", "10Half", "10Full",
        "100Half", "100Full", "1000Full", "",
    ]


def test_parse_int_array():
    assert parser.parse_int_array("state:[1,0,1]", "state") == [1, 0, 1]
    assert parser.parse_int_array("x:[]", "x") == []
    assert parser.parse_int_array("nope", "state") is None
    # A malformed entry yields None rather than a partial array.
    assert parser.parse_int_array("state:[1,foo,1]", "state") is None


def test_parse_int_array_does_not_match_a_similarly_named_array():
    text = "fc_act:[1,1]\nact:[9,9]"
    assert parser.parse_int_array(text, "act") == [9, 9]
    assert parser.parse_int_array(text, "fc_act") == [1, 1]


def test_parse_string_field_tolerates_newlines():
    assert parser.parse_string_field('macStr:[\n"AA:BB"\n]', "macStr") == "AA:BB"
    assert parser.parse_string_field("macStr:[]", "macStr") is None


# ══════════════════════════════════════════════════════════════════════════
# System info
# ══════════════════════════════════════════════════════════════════════════

def test_sg105_system_info(sg105_info):
    info = parser.parse_system_info(sg105_info)
    assert info["model"] == "TL-SG105E 5.0"
    assert info["firmware"] == "1.0.0 Build 20250710 Rel.71066"
    assert info["mac"] == "AA:BB:CC:00:AA:01"
    assert info["ip"] == "192.0.2.10"
    assert info["netmask"] == "255.255.255.0"


def test_sg116_system_info(sg116_info):
    info = parser.parse_system_info(sg116_info)
    assert info["model"] == "TL-SG116E 2.20"
    assert info["firmware"] == "1.0.0 Build 20230505 Rel.70817"
    assert info["mac"] == "AA:BB:CC:00:BB:02"


def test_model_comes_from_hardware_not_description(sg116_info):
    """descriStr is the user-set device name; the model is in hardwareStr.

    bairnhard/ha-tplink-monitor maps descriStr to device_model, which shows the
    model as "SW01" on these switches.
    """
    info = parser.parse_system_info(sg116_info)
    assert info["name"] == "SW01"
    assert info["model"] == "TL-SG116E 2.20"
    assert info["model"] != info["name"]


# ══════════════════════════════════════════════════════════════════════════
# Ports — TL-SG105E (5 ports)
# ══════════════════════════════════════════════════════════════════════════

def test_sg105_port_count(sg105_stats):
    ports = parser.parse_ports(sg105_stats)
    assert [p.port for p in ports] == ["1", "2", "3", "4", "5"]


def test_sg105_trailing_array_entries_are_ignored(sg105_stats):
    """state/link_status carry 7 entries for a 5-port switch.

    Iterating the arrays instead of max_port_num would invent two ports.
    """
    assert parser.parse_max_port_num(sg105_stats) == 5
    assert len(parser.parse_int_array(sg105_stats, "state")) == 7
    assert len(parser.parse_ports(sg105_stats)) == 5


def test_sg105_link_states(sg105_stats):
    by_port = {p.port: p for p in parser.parse_ports(sg105_stats)}
    assert by_port["1"].status == "up"
    assert by_port["1"].speed == "1000M"
    assert by_port["1"].duplex == "Full"
    for down in ("3", "4", "5"):
        assert by_port[down].status == "down"
        assert by_port[down].speed == ""
        assert by_port[down].duplex == ""


def test_sg105_counters(sg105_stats):
    by_port = {p.port: p for p in parser.parse_ports(sg105_stats)}
    assert by_port["1"].tx_packets == 11_350_380
    assert by_port["1"].rx_packets == 13_643_700
    assert by_port["1"].rx_bad_packets == 3_454_002
    assert by_port["1"].tx_bad_packets == 0


def test_down_port_can_retain_counters(sg105_stats):
    """Port 4 is down but holds counters from a previous connection."""
    by_port = {p.port: p for p in parser.parse_ports(sg105_stats)}
    assert by_port["4"].status == "down"
    assert by_port["4"].tx_packets == 1_792_627


# ══════════════════════════════════════════════════════════════════════════
# Ports — TL-SG116E (16 ports)
# ══════════════════════════════════════════════════════════════════════════

def test_sg116_port_count(sg116_stats):
    ports = parser.parse_ports(sg116_stats)
    assert len(ports) == 16
    assert parser.parse_max_port_num(sg116_stats) == 16
    assert len(parser.parse_int_array(sg116_stats, "state")) == 18


def test_sg116_mixed_speeds(sg116_stats):
    by_port = {p.port: p for p in parser.parse_ports(sg116_stats)}
    assert (by_port["2"].speed, by_port["2"].duplex) == ("100M", "Full")
    assert (by_port["4"].speed, by_port["4"].duplex) == ("1000M", "Full")
    assert (by_port["15"].speed, by_port["15"].duplex) == ("100M", "Full")
    assert by_port["1"].status == "down"


def test_sg116_bad_packets_are_large_on_trunk_ports(sg116_stats):
    """RxBadPkt is dominated by VLAN-filtered ingress, not physical errors.

    Confirmed for this network. It is why these are exposed as "bad packets"
    rather than as an error condition: port 4 runs ≈9% and port 13 ≈14% of
    good receives, while ports 14 and 15 are essentially zero.
    """
    by_port = {p.port: p for p in parser.parse_ports(sg116_stats)}
    assert by_port["4"].rx_bad_packets == 37_097_533
    assert by_port["13"].rx_bad_packets == 22_511_165
    assert by_port["14"].rx_bad_packets == 1_036
    assert by_port["4"].tx_bad_packets == 0


# ══════════════════════════════════════════════════════════════════════════
# Port settings overlay
# ══════════════════════════════════════════════════════════════════════════

def test_sg105_settings_overlay(sg105_stats, sg105_setting):
    ports = parser.parse_ports(sg105_stats)
    assert parser.apply_port_settings(sg105_setting, ports) is True
    by_port = {p.port: p for p in ports}
    assert by_port["1"].speed_config == "Auto"
    assert by_port["1"].flow_control == "Off"
    assert by_port["1"].flow_control_config == "Off"
    assert by_port["1"].trunk_group == 0
    # The overlay must not overwrite the negotiated speed from the stats page.
    assert by_port["1"].speed == "1000M"


def test_sg116_settings_overlay(sg116_stats, sg116_setting):
    ports = parser.parse_ports(sg116_stats)
    assert parser.apply_port_settings(sg116_setting, ports) is True
    by_port = {p.port: p for p in ports}
    assert all(p.speed_config == "Auto" for p in ports)
    assert by_port["4"].speed == "1000M"


def test_settings_failure_does_not_lose_ports(sg116_stats):
    """The settings page is supplementary; losing it must not drop ports."""
    ports = parser.parse_ports(sg116_stats)
    assert parser.apply_port_settings("", ports) is False
    assert len(ports) == 16
    assert ports[3].speed == "1000M"


# ══════════════════════════════════════════════════════════════════════════
# Authentication detection
# ══════════════════════════════════════════════════════════════════════════

def test_authentication_marker(sg116_info):
    """logon.cgi returns the login page whether or not credentials were taken.

    So a protected page carrying info_ds is the only reliable success signal.
    Getting this wrong is how a wrong password reads as a working one.
    """
    assert parser.is_authenticated(sg116_info, AUTH_MARKER) is True
    assert parser.is_authenticated("<html>login form</html>", AUTH_MARKER) is False
    assert parser.is_authenticated("", AUTH_MARKER) is False


def test_unauthenticated_stats_page_yields_no_ports():
    """A login page has no max_port_num, so it must not produce phantom ports."""
    assert parser.parse_ports("<html>login</html>") == []
    assert parser.parse_ports("") == []


# ══════════════════════════════════════════════════════════════════════════
# Robustness
# ══════════════════════════════════════════════════════════════════════════

def test_garbage_input_is_survivable():
    assert parser.parse_system_info("") == {}
    assert parser.parse_system_info("<html>nope</html>") == {}
    assert parser.parse_ports("var max_port_num = 4;") == []


def test_arrays_shorter_than_port_count_are_rejected():
    """Trusting a short array would index past the end."""
    html = "var max_port_num = 8; state:[1,1]\nlink_status:[6,6]\npkts:[1,0,2,0]"
    assert parser.parse_ports(html) == []


def test_pkts_shorter_than_ports_leaves_counters_unread():
    html = ("var max_port_num = 2; state:[1,1]\nlink_status:[6,6]\n"
            "pkts:[10,0,20,0]")
    ports = parser.parse_ports(html)
    assert len(ports) == 2
    assert ports[0].tx_packets == 10
    assert ports[1].tx_packets is None    # unread, not zero


def test_disabled_port_reported_as_disabled():
    html = ("var max_port_num = 2; state:[1,0]\nlink_status:[6,0]\n"
            "pkts:[10,0,20,0,0,0,0,0]")
    ports = parser.parse_ports(html)
    assert ports[1].status == "disable"
    assert ports[1].link == "Disabled"


# ══════════════════════════════════════════════════════════════════════════
# Rates (ported logic — reset and unread safety)
# ══════════════════════════════════════════════════════════════════════════

def _port(num="1", tx=None, rx=None):
    return PortData(port=num, status="up", link="Link Up", speed="1000M",
                    duplex="Full", tx_packets=tx, rx_packets=rx)


def test_first_poll_has_no_rate():
    tracker = rates_mod.RateTracker()
    ports = [_port("1", 1000, 2000)]
    tracker.update(ports, 100.0)
    assert ports[0].tx_pps is None and ports[0].rx_pps is None


def test_second_poll_derives_rate():
    tracker = rates_mod.RateTracker()
    tracker.update([_port("1", 1000, 2000)], 100.0)
    ports = [_port("1", 1300, 2600)]
    tracker.update(ports, 130.0)
    assert ports[0].tx_pps == pytest.approx(10.0)
    assert ports[0].rx_pps == pytest.approx(20.0)


def test_counter_reset_yields_no_rate():
    tracker = rates_mod.RateTracker()
    tracker.update([_port("1", 5_000_000, 5_000_000)], 100.0)
    ports = [_port("1", 12, 8)]
    tracker.update(ports, 130.0)
    assert ports[0].tx_pps is None


def test_idle_port_reports_a_real_zero():
    tracker = rates_mod.RateTracker()
    tracker.update([_port("1", 500, 500)], 100.0)
    ports = [_port("1", 500, 500)]
    tracker.update(ports, 130.0)
    assert ports[0].tx_pps == 0.0


# ══════════════════════════════════════════════════════════════════════════
# Options clamping
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (30, 30), (30.0, 30), ("60", 60),
        (1, MIN_SCAN_INTERVAL), (9999, MAX_SCAN_INTERVAL),
        (None, DEFAULT_SCAN_INTERVAL), ("abc", DEFAULT_SCAN_INTERVAL),
        (float("inf"), DEFAULT_SCAN_INTERVAL),
    ],
)
def test_clamp_scan_interval(raw, expected):
    assert options_mod.clamp_scan_interval(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, 0), (None, DEFAULT_ASSUMED_FRAME_BYTES), (-5, 0),
        (1518, 1518), (10, MIN_FRAME_BYTES), (99999, MAX_FRAME_BYTES),
        ("abc", 0), (float("nan"), 0),
    ],
)
def test_clamp_assumed_frame_bytes(raw, expected):
    assert options_mod.clamp_assumed_frame_bytes(raw) == expected


def test_throughput_estimate_is_opt_in():
    assert DEFAULT_ASSUMED_FRAME_BYTES == 0


def test_wire_overhead_is_preamble_plus_gap():
    assert WIRE_OVERHEAD_BYTES == 20

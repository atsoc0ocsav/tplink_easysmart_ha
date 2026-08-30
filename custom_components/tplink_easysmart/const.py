"""Constants for the TP-Link EasySmart switch integration."""

DOMAIN = "tplink_easysmart"
MANUFACTURER = "TP-Link"

DEFAULT_PORT = 80
DEFAULT_USERNAME = "admin"
DEFAULT_SCAN_INTERVAL = 30  # seconds

# Bounds on the polling interval. These switches run a small embedded HTTP
# server that drops connections when polled hard, and their web UI is known to
# hang under sustained load.
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300

CONF_SCAN_INTERVAL = "scan_interval"

# Name used for this switch's devices, and therefore the stem of every entity
# id. Defaults to the name the switch reports in descriStr. It needs to be
# settable because that name is not unique in practice: two EasySmart switches
# at different sites both reported "SW01", so their port child devices both
# came out as "Port 1" and Home Assistant disambiguated the second set with a
# "_2" suffix — leaving entity ids that say nothing about which switch they
# belong to, and whose suffix depends on which config entry was set up first.
CONF_DEVICE_NAME = "device_name"

# Optional estimated-throughput feature. These switches count frames only, so a
# bit rate can be produced solely by assuming an average frame size. 0 disables
# it, which is the default: no assumption is made unless the user makes one.
# Separate per direction, because the two directions of a link routinely carry
# very different frame sizes.
CONF_ASSUMED_TX_FRAME_BYTES = "assumed_tx_frame_bytes"
CONF_ASSUMED_RX_FRAME_BYTES = "assumed_rx_frame_bytes"
DEFAULT_ASSUMED_FRAME_BYTES = 0
MIN_FRAME_BYTES = 64            # smallest legal Ethernet frame
MAX_FRAME_BYTES = 16383

# Preamble + start-of-frame delimiter (8 B) and interframe gap (12 B).
WIRE_OVERHEAD_BYTES = 20

# Endpoints. The "Rpm" pages are the switch's own web UI pages; each embeds a
# JavaScript object holding the data the page renders client-side.
CGI_LOGIN = "/logon.cgi"
PAGE_SYSTEM_INFO = "/SystemInfoRpm.htm"
PAGE_PORT_STATS = "/PortStatisticsRpm.htm"
PAGE_PORT_SETTING = "/PortSettingRpm.htm"

# Marker proving a response is an authenticated page rather than the login form.
# logon.cgi returns the login page HTML whether or not credentials were accepted,
# so this is the only reliable success signal.
AUTH_MARKER = "info_ds"

# Value maps, taken verbatim from the switches' own JavaScript:
#   state_info = new Array("Disabled","Enabled")
#   link_info  = new Array("Link Down","Auto","10Half","10Full",
#                          "100Half","100Full","1000Full","")
# Codes 1 (Auto) and 4 (100Half) are absent from bairnhard/ha-tplink-monitor's
# table, which reports them as Unknown.
PORT_STATE_MAP = {0: "Disabled", 1: "Enabled"}
LINK_STATUS_MAP = {
    0: "Link Down",
    1: "Auto",
    2: "10Half",
    3: "10Full",
    4: "100Half",
    5: "100Full",
    6: "1000Full",
    7: "",
}
FLOW_CONTROL_MAP = {0: "Off", 1: "On"}

# Negotiated link codes that mean "no link".
LINK_DOWN_CODES = frozenset({0, 7})

PORT_STATUS_UP = "up"
PORT_STATUS_DOWN = "down"
PORT_STATUS_DISABLED = "disable"

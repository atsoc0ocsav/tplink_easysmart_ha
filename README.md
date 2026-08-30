# TP-Link EasySmart Switch — Home Assistant Integration

Monitor TP-Link **EasySmart** switches (TL-SG105E, TL-SG116E and relatives) from Home Assistant, over the switch's own web UI. Fully local, no cloud, no extra service.

---

## Supported devices

| Model | Ports | Status |
|-------|-------|--------|
| TL-SG105E 5.0 | 5 | ✅ Confirmed (firmware 1.0.0 Build 20250710) |
| TL-SG116E 2.20 | 16 | ✅ Confirmed (firmware 1.0.0 Build 20230505) |
| TL-SG108E, TL-SG1016DE, TL-SG1024DE and similar | varies | ✅ Likely — same `*Rpm.htm` web UI |

If your switch serves a login page on port 80 and has a **Port Statistics** page, it will very likely work. The port count is read from the switch, not assumed.

---

## Entities

### Switch device

| Entity | Description |
|--------|-------------|
| Model | From `hardwareStr`, e.g. `TL-SG116E 2.20` |
| Firmware | Firmware version string |
| MAC Address | Switch hardware MAC |
| Ports Up | Count of ports with a link |
| Ports Total | Physical port count, read from the switch |

### Port N device *(one per physical port)*

| Entity | Description |
|--------|-------------|
| Link | Binary sensor — `ON` = link up. Carries all port attributes |
| Speed | `10M` · `100M` · `1000M` |
| Duplex | `Full` or `Half` |
| Flow Control | Negotiated flow control |
| TX / RX Packets | Cumulative frame counters |
| TX / RX Bad Packets | The switch's `TxBadPkt` / `RxBadPkt` — **see the note below** |
| TX / RX Rate | Frames per second, derived from consecutive polls |
| TX / RX Throughput (estimated) | Only when you set an assumed frame size — see below |

---

## Bad packets are not necessarily errors

The switch reports `TxBadPkt` and `RxBadPkt`. It is tempting to surface these as "errors" — this integration deliberately does not, because on a trunk port they are dominated by **VLAN-filtered ingress**, not physical faults.

Measured on the switches this was developed against: two trunk ports ran **9.3%** and **14.2%** of good receives as "bad", while access ports on the same switch sat at essentially zero. That is the signature of frames arriving for VLANs the port does not admit, being counted and discarded exactly as configured.

So a high RxBad on a trunk is usually normal. A high RxBad on an **access** port is worth investigating as a cabling or duplex problem. The counters are exposed as diagnostic entities so you can tell the difference yourself; the integration will not guess for you.

---

## Rates: frames per second, and an optional bit-rate estimate

**TX Rate** and **RX Rate** are exact. They are derived from the change in the frame counters between polls.

A rate in *bits* per second **cannot be measured**: no byte or octet counter exists anywhere in this web UI. Converting frames to bits requires an average frame size the switch never reports.

It can be **estimated** if you supply that average. Set **Assumed average TX/RX frame size** in the integration options (`0`, the default, disables each) and each port gains **TX/RX Throughput (estimated)**, computed as `frames/s × (assumed bytes + 20) × 8` — the 20 bytes being preamble, SFD and interframe gap.

Three things to know before trusting those numbers:

- **The two directions differ, often by a lot.** On one measured uplink, one direction averaged 1372 bytes per frame and the other 239 — 5.7× apart. That is why they are configured separately.
- **The right value drifts with the traffic mix.** Sampling one link four times gave 239, 251, 627 and 973 bytes in the same direction. A fixed assumption is wrong most of the time, by a factor that itself moves.
- **Measure it rather than guess.** If the device at the other end of the link reports byte counters (a managed switch, a router, anything with SNMP), the answer is **bytes ÷ packets** over the same interval.

The estimate sensors are named `(estimated)`, carry `estimated: true` and `assumed_frame_bytes` as attributes, and are `MEASUREMENT` rather than cumulative totals, so a wrong assumption cannot contaminate long-term statistics. Treat them as indicative; do not alert on them. The frame-rate sensors carry no such caveat.

A rate is reported as `unknown`, never `0`, when it cannot be known: the first poll after startup, a poll whose statistics read failed, or the single interval spanning a counter reset. An idle port reports a real `0`.

---

## Installation

### Via HACS

1. HACS → Integrations → ⋮ → **Custom repositories**
2. URL: `https://github.com/atsoc0ocsav/tplink_easysmart_ha` · Type: **Integration**
3. Install **TP-Link EasySmart Switch**, then restart Home Assistant
4. **Settings → Devices & Services → Add Integration → TP-Link EasySmart Switch**

### Manual

Copy `custom_components/tplink_easysmart/` into `<config>/custom_components/` and restart.

---

## Setup

| Field | Default | Notes |
|-------|---------|-------|
| Host or IP address | — | e.g. `192.168.1.50` |
| HTTP port | `80` | Change only if you remapped the web UI |
| Username | `admin` | |
| Password | — | |
| Polling interval | `30` s | 10–300 s, changeable later |

Add one entry per switch. The entry's unique id is the switch's MAC, so its IP can change without breaking the entities.

**Keep the interval at 30 s unless you have a reason.** These switches run a tiny embedded HTTP server that drops connections under load; 10 s is the floor for that reason, and polling harder will not make the counters more accurate.

---

## Firmware quirks worth knowing

These are real behaviours of the hardware, found while building this.

**An open session is handed to anyone who asks.** While a session is active, `logon.cgi` returns that live session's cookie to *any* client posting *any* credentials — verified byte-identical on a TL-SG105E, where a deliberately wrong password then read every page. Once the session lapses, the same wrong password is refused.

Two consequences. First, **keep these switches on a management VLAN**: anyone who can reach one can ride an open admin session. Second, the integration cannot promise to catch a wrong password if you have the web UI open in a browser at the same time — the config flow will say so.

**`logon.cgi` always returns the login page.** Success and failure look identical in its response. Authentication is therefore verified by fetching a protected page and checking for real content, which is the only reliable signal.

**The web UI is fragile.** Requests arriving back to back get dropped with no reply, so reads are spaced and retried, and the integration authenticates once rather than per poll. Some models are known to hang their web UI after long uptime; putting management on VLAN 1 is the usual workaround.

**Logins are throttled, and the throttle looks like a wrong password.** After enough login attempts a switch starts dropping the login conversation entirely — `GET /` still returns the login page, but `POST /logon.cgi` followed by a protected page fetch gets no reply. Valid credentials are refused in that state exactly as invalid ones are, so a failed login is *not* evidence of a wrong password.

Two consequences for this integration. It authenticates once and reuses the session rather than logging in per poll, which keeps it well clear of the throttle. And it does not ask you to re-enter credentials on the first refusal: `ConfigEntryAuthFailed` is only raised after several consecutive failures, because a single one is more likely to be another session or the throttle than a bad password.

**Two data-shape traps**, both covered by tests: the `state` / `link_status` / `pkts` arrays carry **more entries than `max_port_num`** (two extra on both confirmed models), so iterating the arrays invents ports that do not exist; and `descriStr` is the **user-set device name**, not the model — the model is in `hardwareStr`.

---

## Development

Page parsing lives in `parser.py` as plain functions, separate from the HTTP layer in `scraper.py`, and the rate and option logic is Home-Assistant-free too. So the test suite needs neither a switch nor Home Assistant:

```bash
pip install pytest
python3 -m pytest tests/ -v
```

Fixtures in `tests/fixtures/<model>/` are real captures from both confirmed models, with MAC and IP addresses replaced by documentation-range values.

**Adding a model?** Capture `SystemInfoRpm.htm`, `PortStatisticsRpm.htm` and `PortSettingRpm.htm`, scrub the addresses, add them as a fixture, and open a PR.

---

## Credits

CGI endpoint knowledge from [bairnhard/ha-tplink-monitor](https://github.com/bairnhard/ha-tplink-monitor) — the login flow and page names came from there, and were then re-verified against real hardware. Note that this integration deliberately differs from it in several ways: it reads the model from `hardwareStr` rather than `descriStr`, includes link-speed codes 1 (`Auto`) and 4 (`100Half`), verifies that login actually succeeded, and does **not** publish `packets × MTU` as a bit rate.

Architecture — the HA-free parsing/rates split, capability-gated entities, and the counter-reset handling — is shared with [horaco_switch_ha](https://github.com/gtrancillo/horaco_switch_ha).

## License

MIT — see [LICENSE](LICENSE).

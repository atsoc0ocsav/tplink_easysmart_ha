"""Async HTTP client for the TP-Link EasySmart web UI.

This module owns the HTTP conversation only; all page parsing lives in
``parser.py`` so it can be unit-tested against captured fixtures.

Auth flow:
  1. ``POST /logon.cgi`` with ``logon=Login&username=…&password=…``, standard
     form encoding. The switch sets an ``H_P_SSID`` cookie.
  2. **Verify by fetching a protected page.** ``logon.cgi`` returns the login
     page HTML whether or not the credentials were accepted, so its response is
     no evidence at all. A page containing ``info_ds`` is the only reliable
     success signal.
  3. ``GET /SystemInfoRpm.htm``   → name, model, MAC, IP, firmware
  4. ``GET /PortStatisticsRpm.htm`` → port state, link status, frame counters
  5. ``GET /PortSettingRpm.htm``  → configured speed, flow control, LAG

These switches run a small embedded HTTP server that closes connections without
replying under even modest load, and the TL-SG116E has a documented history of
hanging its web UI. Requests are therefore spaced, retried, and never issued
concurrently.

.. important::
   The ``session`` passed in MUST have a cookie jar that is not shared with
   anything else — use ``async_create_clientsession`` rather than
   ``async_get_clientsession``. Authentication here is a cookie, and a shared
   jar makes it leak: with Home Assistant's shared session, a second switch (or
   a re-configuration with a wrong password) rides on a session cookie that is
   still valid from an earlier login, and the wrong credentials appear to work.
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from . import parser
from .const import (
    AUTH_MARKER,
    CGI_LOGIN,
    PAGE_PORT_SETTING,
    PAGE_PORT_STATS,
    PAGE_SYSTEM_INFO,
)
from .models import PortData, SwitchData
from .rates import RateTracker

__all__ = ["TPLinkEasySmartClient", "InvalidAuth", "CannotConnect",
           "PortData", "SwitchData"]

_LOGGER = logging.getLogger(__name__)

# Spacing between sequential requests. The web server drops back-to-back
# requests; this is the same rationale as the delay in the Horaco integration.
_REQUEST_DELAY = 0.5

# The server intermittently closes a connection with no reply.
_MAX_ATTEMPTS = 3

_TIMEOUT = aiohttp.ClientTimeout(total=20)


class CannotConnect(Exception):
    """The switch could not be reached, or stopped replying mid-conversation."""


class InvalidAuth(Exception):
    """The switch was reached but rejected the credentials.

    Kept distinct from CannotConnect on purpose: a transport failure during
    verification is NOT evidence of bad credentials, and conflating the two
    produces a confident wrong diagnosis.

    .. warning::
       This firmware only validates credentials when **no session is already
       open**. Verified on a TL-SG105E: with a session active, ``logon.cgi``
       returns that live session's ``H_P_SSID`` to a deliberately wrong
       password, byte-identical to the correct one, and the wrong password then
       reads every page. Once the session lapses, the same wrong password is
       refused.

       Two consequences. Any client that can reach the switch can ride an open
       admin session by posting arbitrary credentials, so keep management on a
       segregated VLAN. And a successful login is only evidence of a correct
       password when nothing else is logged in — which is why the config flow
       cannot promise to catch a wrong password if the user has the web UI open
       in a browser at the same time.
    """


class TPLinkEasySmartClient:
    """Reads a TP-Link EasySmart switch over its web UI."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        http_port: int = 80,
    ) -> None:
        self._session = session
        self.host = host
        self._username = username
        self._password = password
        self._base_url = (
            f"http://{host}:{http_port}" if http_port != 80 else f"http://{host}"
        )
        # Per-client cookie jar, so two switches never share a session.
        self._cookies: dict[str, str] = {}
        self._logged_in = False
        self._rates = RateTracker()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _post_login(self) -> None:
        """Send credentials. Does not, and cannot, confirm they were accepted.

        The session cookie jar is cleared first: without that, a still-valid
        cookie from an earlier login would authenticate the next request no
        matter what password was just sent.
        """
        if self._session.cookie_jar is not None:
            self._session.cookie_jar.clear()
        self._cookies.clear()

        payload = {
            "logon": "Login",
            "username": self._username,
            "password": self._password,
        }
        try:
            async with self._session.post(
                f"{self._base_url}{CGI_LOGIN}",
                data=payload,
                headers={"Referer": f"{self._base_url}/"},
                timeout=_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                await resp.read()
                for key, morsel in resp.cookies.items():
                    self._cookies[key] = morsel.value
        except aiohttp.ClientError as exc:
            raise CannotConnect(f"Login request to {self.host} failed: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise CannotConnect(f"Login request to {self.host} timed out") from exc

    async def _get(self, path: str) -> str:
        """GET a page, retrying the server's intermittent empty replies."""
        last: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            await asyncio.sleep(_REQUEST_DELAY)
            try:
                async with self._session.get(
                    f"{self._base_url}{path}",
                    headers={"Referer": f"{self._base_url}/"},
                    cookies=self._cookies,
                    timeout=_TIMEOUT,
                ) as resp:
                    resp.raise_for_status()
                    return await resp.text(encoding="utf-8", errors="replace")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last = exc
                _LOGGER.debug(
                    "[%s] GET %s attempt %d/%d failed: %s",
                    self.host, path, attempt, _MAX_ATTEMPTS, exc,
                )
        raise CannotConnect(f"GET {path} on {self.host} failed: {last}")

    async def _login_and_verify(self) -> str:
        """Authenticate and return the system-info page.

        Raises InvalidAuth only when the switch answered and served the login
        form instead of a real page. A transport failure raises CannotConnect,
        because it says nothing about whether the credentials are valid.
        """
        await self._post_login()
        html = await self._get(PAGE_SYSTEM_INFO)      # raises CannotConnect
        if not parser.is_authenticated(html, AUTH_MARKER):
            self._logged_in = False
            raise InvalidAuth(
                f"{self.host} returned the login page after authenticating; "
                "check the username and password"
            )
        self._logged_in = True
        return html

    async def _authenticated_get(self, path: str, marker: str) -> str:
        """GET a protected page, authenticating only when actually necessary.

        Logging in on every poll would double the request count against a web
        server that already drops requests under load, so the session is reused
        until a response comes back as the login form.
        """
        if not self._logged_in:
            info_html = await self._login_and_verify()
            if path == PAGE_SYSTEM_INFO:
                # Already fetched and validated during login; don't refetch.
                return info_html

        html = await self._get(path)
        if parser.is_authenticated(html, marker):
            return html

        # Session lapsed — re-authenticate once, then give up rather than loop.
        _LOGGER.debug("[%s] Session lapsed on %s, re-authenticating", self.host, path)
        self._logged_in = False
        await self._login_and_verify()
        html = await self._get(path)
        if not parser.is_authenticated(html, marker):
            self._logged_in = False
            raise InvalidAuth(
                f"{self.host} served the login page for {path} even after "
                "re-authenticating"
            )
        return html

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def async_validate(self) -> SwitchData:
        """One-shot check used by the config flow."""
        info_html = await self._login_and_verify()
        info = parser.parse_system_info(info_html)
        return SwitchData(
            host=self.host,
            name=info.get("name", ""),
            model=info.get("model", ""),
            mac=info.get("mac", ""),
            ip=info.get("ip", ""),
            firmware=info.get("firmware", ""),
            available=True,
        )

    async def async_update(self) -> SwitchData:
        """Fetch a full snapshot.

        Re-authenticates only when the existing session has lapsed.
        """
        info_html = await self._authenticated_get(PAGE_SYSTEM_INFO, AUTH_MARKER)
        info = parser.parse_system_info(info_html)

        stats_html = await self._authenticated_get(PAGE_PORT_STATS, "max_port_num")

        ports: list[PortData] = parser.parse_ports(stats_html)
        if not ports:
            raise CannotConnect(
                f"No ports parsed from {self.host}; the statistics page may have "
                "changed format — please report the model and firmware"
            )

        # Supplementary: a failure here must not lose the ports.
        try:
            setting_html = await self._authenticated_get(PAGE_PORT_SETTING, "spd_cfg")
            parser.apply_port_settings(setting_html, ports)
        except (CannotConnect, InvalidAuth) as exc:
            _LOGGER.debug("[%s] Port settings page unavailable: %s", self.host, exc)

        self._rates.update(ports, time.monotonic())

        has_bad = any(
            p.tx_bad_packets is not None or p.rx_bad_packets is not None
            for p in ports
        )

        return SwitchData(
            host=self.host,
            name=info.get("name", ""),
            model=info.get("model", ""),
            mac=info.get("mac", ""),
            ip=info.get("ip", ""),
            netmask=info.get("netmask", ""),
            gateway=info.get("gateway", ""),
            firmware=info.get("firmware", ""),
            port_count=len(ports),
            ports=ports,
            available=True,
            has_bad_packet_counters=has_bad,
            has_byte_counters=False,   # no byte counter exists on this hardware
        )

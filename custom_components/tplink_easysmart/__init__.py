"""TP-Link EasySmart switch integration for Home Assistant.

Reads TL-SG105E / TL-SG116E and similar EasySmart switches over their web UI.
No byte counters exist on this hardware, so nothing here synthesises a bit rate
— see the README.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ASSUMED_RX_FRAME_BYTES,
    CONF_ASSUMED_TX_FRAME_BYTES,
    CONF_SCAN_INTERVAL,
    DEFAULT_ASSUMED_FRAME_BYTES,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .options import clamp_assumed_frame_bytes, clamp_scan_interval
from .scraper import CannotConnect, InvalidAuth, SwitchData, TPLinkEasySmartClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


def resolve_scan_interval(entry: ConfigEntry) -> int:
    """Polling interval from options, clamped to the safe range."""
    return clamp_scan_interval(
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )


def resolve_assumed_frame_bytes(entry: ConfigEntry, direction: str) -> int:
    """Assumed average frame size for one direction, 0 when off."""
    key = (
        CONF_ASSUMED_TX_FRAME_BYTES if direction == "tx"
        else CONF_ASSUMED_RX_FRAME_BYTES
    )
    return clamp_assumed_frame_bytes(
        entry.options.get(key, DEFAULT_ASSUMED_FRAME_BYTES)
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one switch from a config entry."""
    # A dedicated session per entry. Authentication here is a cookie, and this
    # firmware hands an open session to anyone who asks — sharing Home
    # Assistant's session jar would let two switches, or a wrong password, ride
    # each other's login.
    session = async_create_clientsession(hass)

    client = TPLinkEasySmartClient(
        session=session,
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        http_port=entry.data.get(CONF_PORT, DEFAULT_PORT),
    )

    coordinator = TPLinkEasySmartCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Without this, editing an option appears to work but has no effect until
    # Home Assistant restarts.
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply an options change without waiting for a restart."""
    coordinator: TPLinkEasySmartCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        return

    # Toggling the throughput estimate adds or removes entities, which only a
    # reload can do. A plain interval change is applied in place so entities
    # keep their state and counter history.
    data = coordinator.data
    was = max(data.assumed_tx_frame_bytes, data.assumed_rx_frame_bytes) if data else 0
    now = max(
        resolve_assumed_frame_bytes(entry, "tx"),
        resolve_assumed_frame_bytes(entry, "rx"),
    )
    if (was == 0) != (now == 0):
        _LOGGER.debug(
            "[%s] Estimated throughput toggled (%s -> %s); reloading entry",
            entry.data[CONF_HOST], was, now,
        )
        await hass.config_entries.async_reload(entry.entry_id)
        return

    seconds = resolve_scan_interval(entry)
    if coordinator.update_interval != timedelta(seconds=seconds):
        _LOGGER.debug(
            "[%s] Polling interval changed to %s s", entry.data[CONF_HOST], seconds
        )
        coordinator.update_interval = timedelta(seconds=seconds)
        await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return ok


class TPLinkEasySmartCoordinator(DataUpdateCoordinator[SwitchData]):
    """Polls one switch at the configured interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: TPLinkEasySmartClient,
        entry: ConfigEntry,
    ) -> None:
        self.client = client
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{client.host}",
            update_interval=timedelta(seconds=resolve_scan_interval(entry)),
        )

    async def _async_update_data(self) -> SwitchData:
        try:
            data = await self.client.async_update()
        except InvalidAuth as exc:
            # Re-authentication will not help; prompt the user instead.
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except CannotConnect as exc:
            raise UpdateFailed(str(exc)) from exc

        # Read per poll rather than cached, so an options change applies to the
        # next update without a reload.
        data.assumed_tx_frame_bytes = resolve_assumed_frame_bytes(self.entry, "tx")
        data.assumed_rx_frame_bytes = resolve_assumed_frame_bytes(self.entry, "rx")
        return data

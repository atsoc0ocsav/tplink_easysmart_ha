"""Config flow for the TP-Link EasySmart switch integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_ASSUMED_RX_FRAME_BYTES,
    CONF_DEVICE_NAME,
    CONF_ASSUMED_TX_FRAME_BYTES,
    CONF_SCAN_INTERVAL,
    DEFAULT_ASSUMED_FRAME_BYTES,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_FRAME_BYTES,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .scraper import CannotConnect, InvalidAuth, SwitchData, TPLinkEasySmartClient

_LOGGER = logging.getLogger(__name__)

CONNECTION_KEYS = (CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD)


def _interval_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_SCAN_INTERVAL,
            max=MAX_SCAN_INTERVAL,
            step=5,
            unit_of_measurement="s",
            mode=NumberSelectorMode.BOX,
        )
    )


def _frame_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0,
            max=MAX_FRAME_BYTES,
            step=1,
            unit_of_measurement="B",
            mode=NumberSelectorMode.BOX,
        )
    )


STEP_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(
        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
    ): _interval_selector(),
    # Left blank, the switch's own reported name is used. Set it when you have
    # more than one of these switches: they commonly all report "SW01".
    vol.Optional(CONF_DEVICE_NAME, default=""): str,
})


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> SwitchData:
    """Log in once and read the system-info page.

    A dedicated session is used so this cannot ride an existing login.
    """
    client = TPLinkEasySmartClient(
        session=async_create_clientsession(hass),
        host=data[CONF_HOST],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        http_port=data.get(CONF_PORT, DEFAULT_PORT),
    )
    return await client.async_validate()


class TPLinkEasySmartConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for one switch."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                switch = await _validate(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating the switch")
                errors["base"] = "unknown"
            else:
                # The MAC is stable across IP changes; fall back to the host
                # when the switch did not report one.
                unique = switch.mac or user_input[CONF_HOST]
                await self.async_set_unique_id(unique.lower())
                self._abort_if_unique_id_configured()

                title = switch.model or "TP-Link EasySmart switch"
                if switch.name:
                    title = f"{switch.name} ({switch.model})" if switch.model else switch.name
                return self.async_create_entry(
                    title=title,
                    data={k: user_input[k] for k in CONNECTION_KEYS if k in user_input},
                    options={
                        CONF_DEVICE_NAME: (
                            user_input.get(CONF_DEVICE_NAME) or ""
                        ).strip(),
                        CONF_SCAN_INTERVAL: int(
                            user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                        ),
                        CONF_ASSUMED_TX_FRAME_BYTES: DEFAULT_ASSUMED_FRAME_BYTES,
                        CONF_ASSUMED_RX_FRAME_BYTES: DEFAULT_ASSUMED_FRAME_BYTES,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Triggered when the switch starts rejecting the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _validate(self.hass, data)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    @staticmethod
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> TPLinkEasySmartOptionsFlow:
        return TPLinkEasySmartOptionsFlow()


class TPLinkEasySmartOptionsFlow(config_entries.OptionsFlow):
    """Polling interval and the optional throughput estimate."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_DEVICE_NAME: (
                        user_input.get(CONF_DEVICE_NAME) or ""
                    ).strip(),
                    CONF_SCAN_INTERVAL: int(
                        user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ),
                    CONF_ASSUMED_TX_FRAME_BYTES: int(
                        user_input.get(
                            CONF_ASSUMED_TX_FRAME_BYTES, DEFAULT_ASSUMED_FRAME_BYTES
                        )
                    ),
                    CONF_ASSUMED_RX_FRAME_BYTES: int(
                        user_input.get(
                            CONF_ASSUMED_RX_FRAME_BYTES, DEFAULT_ASSUMED_FRAME_BYTES
                        )
                    ),
                },
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_DEVICE_NAME,
                    default=options.get(CONF_DEVICE_NAME, ""),
                ): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): _interval_selector(),
                vol.Optional(
                    CONF_ASSUMED_TX_FRAME_BYTES,
                    default=options.get(
                        CONF_ASSUMED_TX_FRAME_BYTES, DEFAULT_ASSUMED_FRAME_BYTES
                    ),
                ): _frame_selector(),
                vol.Optional(
                    CONF_ASSUMED_RX_FRAME_BYTES,
                    default=options.get(
                        CONF_ASSUMED_RX_FRAME_BYTES, DEFAULT_ASSUMED_FRAME_BYTES
                    ),
                ): _frame_selector(),
            }),
        )

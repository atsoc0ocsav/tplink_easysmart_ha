"""Binary sensor platform — per-port link state."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TPLinkEasySmartCoordinator
from .const import DOMAIN, PORT_STATUS_UP
from .models import PortData
from .sensor import port_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TPLinkEasySmartCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return
    async_add_entities(
        PortLinkBinarySensor(coordinator, p.port) for p in coordinator.data.ports
    )


class PortLinkBinarySensor(
    CoordinatorEntity[TPLinkEasySmartCoordinator], BinarySensorEntity
):
    """ON when the port has a link, OFF when down or administratively disabled."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Link"

    def __init__(
        self, coordinator: TPLinkEasySmartCoordinator, port_num: str
    ) -> None:
        super().__init__(coordinator)
        self._port_num = port_num
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.host}_port{port_num}_link"
        self._attr_device_info = port_device_info(coordinator, port_num)

    def _port(self) -> PortData | None:
        data = self.coordinator.data
        if not data:
            return None
        return next((p for p in data.ports if p.port == self._port_num), None)

    @property
    def is_on(self) -> bool | None:
        port = self._port()
        return port.status == PORT_STATUS_UP if port else None

    @property
    def icon(self) -> str:
        port = self._port()
        return "mdi:ethernet" if (port and port.status == PORT_STATUS_UP) else "mdi:ethernet-off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        port = self._port()
        if port is None:
            return {}
        attrs: dict[str, Any] = {
            "status": port.status,
            "link": port.link,
            "speed": port.speed,
            "duplex": port.duplex,
            "admin_state": port.admin_state,
            "flow_control": port.flow_control,
        }
        # Only advertise counters that were actually read — None means "not
        # read", which is distinct from a real zero.
        for name, value in (
            ("tx_packets", port.tx_packets),
            ("rx_packets", port.rx_packets),
            ("tx_bad_packets", port.tx_bad_packets),
            ("rx_bad_packets", port.rx_bad_packets),
        ):
            if value is not None:
                attrs[name] = value
        if port.tx_pps is not None:
            attrs["tx_packets_per_second"] = round(port.tx_pps, 2)
        if port.rx_pps is not None:
            attrs["rx_packets_per_second"] = round(port.rx_pps, 2)
        if port.speed_config:
            attrs["speed_config"] = port.speed_config
        if port.trunk_group:
            attrs["trunk_group"] = port.trunk_group
        return attrs

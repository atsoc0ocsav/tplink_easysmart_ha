"""Sensor platform for TP-Link EasySmart switches.

One parent device for the switch, one child device per port.

Entities are created only for data the switch actually reports. These switches
count frames and never bytes, so there is no byte or throughput sensor unless
the user explicitly supplies an assumed average frame size — and then it is
labelled an estimate.

Naming note: the switch's ``TxBadPkt`` / ``RxBadPkt`` counters are exposed as
**Bad Packets**, not "Errors". On a trunk port they are dominated by
VLAN-filtered ingress rather than physical faults — confirmed on the network
these were developed against, where two trunk ports ran 9% and 14% of good
receives while access ports sat near zero. Calling them errors would send people
looking for a cabling problem that is not there.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfDataRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TPLinkEasySmartCoordinator
from .const import (
    DOMAIN,
    MANUFACTURER,
    PORT_STATUS_UP,
    WIRE_OVERHEAD_BYTES,
)
from .models import PortData, SwitchData

_LOGGER = logging.getLogger(__name__)


def switch_device_info(coordinator: TPLinkEasySmartCoordinator) -> DeviceInfo:
    """DeviceInfo for the switch itself."""
    d = coordinator.data
    identifier = (d.mac if d and d.mac else coordinator.client.host).lower()
    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        name=(d.name or f"Switch {coordinator.client.host}") if d else
             f"Switch {coordinator.client.host}",
        manufacturer=MANUFACTURER,
        model=(d.model or None) if d else None,
        sw_version=(d.firmware or None) if d else None,
        connections={("mac", d.mac)} if d and d.mac else set(),
        configuration_url=f"http://{coordinator.client.host}",
    )


def port_device_info(
    coordinator: TPLinkEasySmartCoordinator, port_num: str
) -> DeviceInfo:
    """DeviceInfo for one port, hung off the switch."""
    d = coordinator.data
    parent = (d.mac if d and d.mac else coordinator.client.host).lower()
    return DeviceInfo(
        identifiers={(DOMAIN, f"{parent}_port{port_num}")},
        name=f"Port {port_num}",
        manufacturer=MANUFACTURER,
        via_device=(DOMAIN, parent),
    )


# ────────────────────────────────────────────────────────────────────────────
# Switch-level sensors
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class SwitchSensorDesc(SensorEntityDescription):
    value_fn: Callable[[SwitchData], Any] | None = None
    exists_fn: Callable[[SwitchData], bool] = lambda d: True


SWITCH_SENSORS: tuple[SwitchSensorDesc, ...] = (
    SwitchSensorDesc(
        key="model",
        name="Model",
        icon="mdi:switch",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.model or None,
        exists_fn=lambda d: bool(d.model),
    ),
    SwitchSensorDesc(
        key="firmware",
        name="Firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.firmware or None,
        exists_fn=lambda d: bool(d.firmware),
    ),
    SwitchSensorDesc(
        key="mac_address",
        name="MAC Address",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.mac or None,
        exists_fn=lambda d: bool(d.mac),
    ),
    SwitchSensorDesc(
        key="ports_up",
        name="Ports Up",
        icon="mdi:ethernet",
        native_unit_of_measurement="ports",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for p in d.ports if p.status == PORT_STATUS_UP),
    ),
    SwitchSensorDesc(
        key="ports_total",
        name="Ports Total",
        icon="mdi:ethernet",
        native_unit_of_measurement="ports",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.port_count,
    ),
)


# ────────────────────────────────────────────────────────────────────────────
# Per-port sensors
# ────────────────────────────────────────────────────────────────────────────

def _estimated_bps(pps: float | None, assumed_frame_bytes: int) -> float | None:
    """Frames/s → bits/s given an assumed frame size, or None."""
    if pps is None or assumed_frame_bytes <= 0:
        return None
    return pps * (assumed_frame_bytes + WIRE_OVERHEAD_BYTES) * 8


def _estimate_attrs(assumed: int) -> dict[str, Any]:
    return {
        "estimated": True,
        "assumed_frame_bytes": assumed,
        "on_wire_bytes_per_frame": assumed + WIRE_OVERHEAD_BYTES,
        "includes_wire_overhead": True,
    }


@dataclass
class PortSensorDesc(SensorEntityDescription):
    value_fn: Callable[[PortData], Any] | None = None
    switch_value_fn: Callable[[PortData, SwitchData], Any] | None = None
    exists_fn: Callable[[SwitchData], bool] = lambda d: True
    port_attrs_fn: Callable[[PortData, SwitchData], dict[str, Any]] | None = None


PORT_SENSORS: tuple[PortSensorDesc, ...] = (
    PortSensorDesc(
        key="speed",
        name="Speed",
        icon="mdi:speedometer",
        value_fn=lambda p: p.speed or None,
    ),
    PortSensorDesc(
        key="duplex",
        name="Duplex",
        icon="mdi:transfer",
        value_fn=lambda p: p.duplex or None,
    ),
    PortSensorDesc(
        key="flow_control",
        name="Flow Control",
        icon="mdi:swap-horizontal",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.flow_control or None,
    ),
    PortSensorDesc(
        key="tx_packets",
        name="TX Packets",
        icon="mdi:arrow-up-circle-outline",
        native_unit_of_measurement="packets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.tx_packets,
    ),
    PortSensorDesc(
        key="rx_packets",
        name="RX Packets",
        icon="mdi:arrow-down-circle-outline",
        native_unit_of_measurement="packets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda p: p.rx_packets,
    ),
    # Named "Bad Packets" rather than "Errors" on purpose — see the module
    # docstring. These are largely VLAN-filtered ingress on trunk ports.
    PortSensorDesc(
        key="tx_bad_packets",
        name="TX Bad Packets",
        icon="mdi:package-variant-closed-remove",
        native_unit_of_measurement="packets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.tx_bad_packets,
        exists_fn=lambda d: d.has_bad_packet_counters,
    ),
    PortSensorDesc(
        key="rx_bad_packets",
        name="RX Bad Packets",
        icon="mdi:package-variant-closed-remove",
        native_unit_of_measurement="packets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.rx_bad_packets,
        exists_fn=lambda d: d.has_bad_packet_counters,
    ),
    # Frame rate, not bit rate. Exact — no assumption involved.
    PortSensorDesc(
        key="tx_pps",
        name="TX Rate",
        icon="mdi:upload-network-outline",
        native_unit_of_measurement="packets/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda p: p.tx_pps,
    ),
    PortSensorDesc(
        key="rx_pps",
        name="RX Rate",
        icon="mdi:download-network-outline",
        native_unit_of_measurement="packets/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda p: p.rx_pps,
    ),
    # Opt-in estimates, off unless the user supplies an average frame size.
    PortSensorDesc(
        key="tx_throughput_estimated",
        name="TX Throughput (estimated)",
        icon="mdi:upload-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.KILOBITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        switch_value_fn=lambda p, d: _estimated_bps(p.tx_pps, d.assumed_tx_frame_bytes),
        exists_fn=lambda d: d.assumed_tx_frame_bytes > 0,
        port_attrs_fn=lambda p, d: _estimate_attrs(d.assumed_tx_frame_bytes),
    ),
    PortSensorDesc(
        key="rx_throughput_estimated",
        name="RX Throughput (estimated)",
        icon="mdi:download-network",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.KILOBITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        switch_value_fn=lambda p, d: _estimated_bps(p.rx_pps, d.assumed_rx_frame_bytes),
        exists_fn=lambda d: d.assumed_rx_frame_bytes > 0,
        port_attrs_fn=lambda p, d: _estimate_attrs(d.assumed_rx_frame_bytes),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TPLinkEasySmartCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    entities: list[SensorEntity] = []

    for desc in SWITCH_SENSORS:
        if data is None or desc.exists_fn(data):
            entities.append(SwitchLevelSensor(coordinator, desc))

    if data:
        for port in data.ports:
            for desc in PORT_SENSORS:
                if desc.exists_fn(data):
                    entities.append(PortLevelSensor(coordinator, port.port, desc))

    async_add_entities(entities)


class SwitchLevelSensor(CoordinatorEntity[TPLinkEasySmartCoordinator], SensorEntity):
    """A sensor attached to the switch device."""

    entity_description: SwitchSensorDesc

    def __init__(
        self, coordinator: TPLinkEasySmartCoordinator, desc: SwitchSensorDesc
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = desc
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.host}_{desc.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = switch_device_info(coordinator)

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        return self.entity_description.value_fn(data) if data else None


class PortLevelSensor(CoordinatorEntity[TPLinkEasySmartCoordinator], SensorEntity):
    """A sensor attached to a port child device."""

    entity_description: PortSensorDesc

    def __init__(
        self,
        coordinator: TPLinkEasySmartCoordinator,
        port_num: str,
        desc: PortSensorDesc,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = desc
        self._port_num = port_num
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.client.host}_port{port_num}_{desc.key}"
        )
        self._attr_has_entity_name = True
        self._attr_device_info = port_device_info(coordinator, port_num)

    def _port(self) -> PortData | None:
        data = self.coordinator.data
        if not data:
            return None
        return next((p for p in data.ports if p.port == self._port_num), None)

    @property
    def native_value(self) -> Any:
        port = self._port()
        if port is None:
            return None
        desc = self.entity_description
        if desc.switch_value_fn is not None:
            data = self.coordinator.data
            return desc.switch_value_fn(port, data) if data else None
        return desc.value_fn(port)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        port = self._port()
        if port is None:
            return {}
        attrs: dict[str, Any] = {"status": port.status, "link": port.link}
        if port.admin_state:
            attrs["admin_state"] = port.admin_state
        if port.speed_config:
            attrs["speed_config"] = port.speed_config
        if port.trunk_group:
            attrs["trunk_group"] = port.trunk_group

        fn = self.entity_description.port_attrs_fn
        if fn is not None and self.coordinator.data:
            attrs.update(fn(port, self.coordinator.data))
        return attrs

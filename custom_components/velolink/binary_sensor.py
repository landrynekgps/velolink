"""Binary sensor platform for Velolink."""

from __future__ import annotations

import logging
from typing import Callable, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    NODE_KIND_INPUT,
    NODE_KIND_VELOSWITCH,
    NODE_KIND_VELOMOTION,
    DEVICE_CLASS_INPUT_MAP,
    POLARITY_NC,
    signal_new_node,
    signal_device_name_updated,
)
from .hub import VelolinkHub, VelolinkNode
from .storage import VelolinkStorage

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES: Final[int] = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up binary sensors."""
    hub: VelolinkHub = hass.data[DOMAIN][entry.entry_id]
    storage: VelolinkStorage = hass.data[DOMAIN][f"{entry.entry_id}_storage"]
    created: set[str] = set()

    @callback
    def _handle_new_node(node: VelolinkNode) -> None:
        _LOGGER.debug("New node for binary_sensor: %s", node)
        node_kinds = (NODE_KIND_INPUT, NODE_KIND_VELOSWITCH, NODE_KIND_VELOMOTION)
        if node.kind not in node_kinds:
            return

        entities = []
        for ch in range(node.channels):
            uid = f"{node.bus_id}-{node.address}-in-{ch}"
            if uid in created:
                _LOGGER.debug("Binary sensor already exists: %s", uid)
                continue
            created.add(uid)
            entities.append(
                VelolinkInputEntity(hass, entry.entry_id, hub, storage, node, ch)
            )

        if entities:
            _LOGGER.info("Adding %d binary sensor entities", len(entities))
            async_add_entities(entities)

    unsub = async_dispatcher_connect(
        hass, signal_new_node(entry.entry_id), _handle_new_node
    )
    entry.async_on_unload(unsub)


class VelolinkInputEntity(BinarySensorEntity):
    """Binary sensor for Velolink input."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        hub: VelolinkHub,
        storage: VelolinkStorage,
        node: VelolinkNode,
        ch: int,
    ) -> None:
        """Initialize entity."""
        self._hass = hass
        self._entry_id = entry_id
        self._hub = hub
        self._storage = storage
        self._node = node
        self._ch = ch
        self._state = False
        self._unsub: Callable[[], None] | None = None
        self._unsub_name_update: Callable[[], None] | None = None

        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from storage."""
        cfg = self._storage.get_channel_config(
            self._node.bus_id, self._node.address, "in", self._ch
        )
        self._device_class_key = cfg.get("device_class", "none")
        self._polarity = cfg.get("polarity", "NO")
        _LOGGER.debug(
            "Loaded config for %s: device_class=%s, polarity=%s",
            self.unique_id,
            self._device_class_key,
            self._polarity,
        )

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._node.bus_id}-{self._node.address}-in-{self._ch}"

    @property
    def name(self) -> str:
        """Return name."""
        custom_name = self._storage.get_device_name(
            self._node.bus_id, self._node.address
        )
        if custom_name:
            return f"{custom_name} IN {self._ch}"

        if self._node.kind == NODE_KIND_VELOSWITCH:
            return f"VeloSwitch {self._node.address}:{self._ch}"

        if self._node.kind == NODE_KIND_VELOMOTION:
            return f"VeloMotion {self._node.address}:{self._ch}"  # FIX: Dodano numer kanału

        return f"Velolink IN {self._node.address}:{self._ch}"

    @property
    def is_on(self) -> bool:
        """Return state."""
        if self._polarity == POLARITY_NC:
            return not self._state
        return self._state

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        """Return device class."""
        return DEVICE_CLASS_INPUT_MAP.get(self._device_class_key)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        custom_name = self._storage.get_device_name(
            self._node.bus_id, self._node.address
        )

        identifier = (DOMAIN, f"{self._node.bus_id}-{self._node.address}")
        device_name = (
            custom_name or f"Velolink {self._node.kind.title()} {self._node.address}"
        )

        return DeviceInfo(
            identifiers={identifier},
            name=device_name,
            manufacturer=self._node.manufacturer,
            model=self._node.model or f"IO-{self._node.kind.upper()}",
            sw_version=self._node.sw_version,
            hw_version=self._node.hw_version,
            suggested_area=self._node.suggested_area,
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes."""
        return {
            "bus": self._node.bus_id,
            "address": self._node.address,
            "channel": self._ch,
            "polarity": self._polarity,
            "device_class_config": self._device_class_key,
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity added to hass."""

        @callback
        def _on_change(val: bool) -> None:
            self._state = val
            self.async_write_ha_state()

        self._unsub = self._hub.subscribe_input(
            self._node.bus_id, self._node.address, self._ch, _on_change
        )
        _LOGGER.debug("Subscribed to input changes for %s", self.unique_id)

        @callback
        def _on_name_update(data: dict) -> None:
            if (
                data["bus_id"] == self._node.bus_id
                and data["address"] == self._node.address
            ):
                _LOGGER.debug("Name update received for %s, refreshing state", self.unique_id)
                self.async_write_ha_state()

        self._unsub_name_update = async_dispatcher_connect(
            self._hass, signal_device_name_updated(self._entry_id), _on_name_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_name_update:
            self._unsub_name_update()
            self._unsub_name_update = None
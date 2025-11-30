"""Light platform for Velolink."""

from __future__ import annotations

import logging
from typing import Any, Callable, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_BRIGHTNESS,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    NODE_KIND_PWM,
    NODE_KIND_VELODIMMER,
    signal_new_node,
    # USUNIĘTO: signal_device_name_updated,
)
from .hub import VelolinkHub, VelolinkNode
from .storage import VelolinkStorage

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES: Final[int] = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up lights."""
    hub: VelolinkHub = hass.data[DOMAIN][entry.entry_id]
    storage: VelolinkStorage = hass.data[DOMAIN][f"{entry.entry_id}_storage"]
    created: set[str] = set()

    @callback
    def _handle_new_node(node: VelolinkNode) -> None:
        if node.kind == NODE_KIND_PWM:
            entities = []
            for ch in range(node.channels):
                uid = f"{node.bus_id}-{node.address}-pwm-{ch}"
                if uid in created:
                    continue
                created.add(uid)
                entities.append(
                    VelolinkLightEntity(hass, entry.entry_id, hub, storage, node, ch)
                )

            if entities:
                async_add_entities(entities)

        elif node.kind == NODE_KIND_VELODIMMER:
            entities = []
            for ch in range(node.channels):
                uid = f"{node.bus_id}-{node.address}-dimmer-{ch}"
                if uid in created:
                    continue
                created.add(uid)
                entities.append(
                    VeloDimmerEntity(hass, entry.entry_id, hub, storage, node, ch)
                )

            if entities:
                async_add_entities(entities)

    unsub = async_dispatcher_connect(
        hass, signal_new_node(entry.entry_id), _handle_new_node
    )
    entry.async_on_unload(unsub)


class VelolinkLightEntity(LightEntity):
    """Light entity for PWM output."""

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
        self._is_on = False
        self._brightness = 255
        self._unsub: Callable[[], None] | None = None
        # USUNIĘTO: self._unsub_name_update: Callable[[], None] | None = None

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._node.bus_id}-{self._node.address}-pwm-{self._ch}"

    @property
    def name(self) -> str:
        """Return name."""
        custom_name = self._storage.get_device_name(
            self._node.bus_id, self._node.address
        )
        if custom_name:
            return f"{custom_name} PWM {self._ch}"
        return f"Velolink PWM {self._node.address}:{self._ch}"

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return supported color modes."""
        return {ColorMode.BRIGHTNESS}

    @property
    def color_mode(self) -> ColorMode:
        """Return color mode."""
        return ColorMode.BRIGHTNESS

    @property
    def is_on(self) -> bool:
        """Return if light is on."""
        return self._is_on

    @property
    def brightness(self) -> int | None:
        """Return brightness."""
        return self._brightness if self._is_on else None

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
            model=self._node.model or "IO-PWM",
            sw_version=self._node.sw_version,
            hw_version=self._node.hw_version,
            suggested_area=self._node.suggested_area,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on light."""
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]

        if not self._is_on and ATTR_BRIGHTNESS not in kwargs:
            self._brightness = 255

        await self._hub.async_set_pwm(
            self._node.bus_id, self._node.address, self._ch, self._brightness
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off light."""
        await self._hub.async_set_pwm(
            self._node.bus_id, self._node.address, self._ch, 0
        )

    async def async_added_to_hass(self) -> None:
        """Handle entity added."""

        @callback
        def _on_change(val: int) -> None:
            self._brightness = max(1, min(255, val))
            self._is_on = self._brightness > 0
            self.async_write_ha_state()

        self._unsub = self._hub.subscribe_pwm(
            self._node.bus_id, self._node.address, self._ch, _on_change
        )

        # USUNIĘTO: Subskrypcja aktualizacji nazwy
        # @callback
        # def _on_name_update(data: dict) -> None:
        #     ...

        # self._unsub_name_update = async_dispatcher_connect(
        #     self._hass, signal_device_name_updated(self._entry_id), _on_name_update
        # )

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        # USUNIĘTO: Anulowanie subskrypcji nazwy
        # if self._unsub_name_update:
        #     self._unsub_name_update()
        #     self._unsub_name_update = None


class VeloDimmerEntity(LightEntity):
    """VeloDimmer - wall dimmer with encoder and button."""

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

        self._is_on = False
        self._brightness = 255
        self._last_encoder_time = None

        self._unsub_button: Callable[[], None] | None = None
        self._unsub_encoder: Callable[[], None] | None = None
        self._unsub_pwm: Callable[[], None] | None = None
        # USUNIĘTO: self._unsub_name_update: Callable[[], None] | None = None

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._node.bus_id}-{self._node.address}-dimmer-{self._ch}"

    @property
    def name(self) -> str:
        """Return name."""
        custom_name = self._storage.get_device_name(
            self._node.bus_id, self._node.address
        )
        if custom_name:
            return custom_name
        return f"VeloDimmer {self._node.address}"

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return supported color modes."""
        return {ColorMode.BRIGHTNESS}

    @property
    def color_mode(self) -> ColorMode:
        """Return color mode."""
        return ColorMode.BRIGHTNESS

    @property
    def is_on(self) -> bool:
        """Return if light is on."""
        return self._is_on

    @property
    def brightness(self) -> int | None:
        """Return brightness."""
        return self._brightness if self._is_on else None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        custom_name = self._storage.get_device_name(
            self._node.bus_id, self._node.address
        )

        identifier = (DOMAIN, f"{self._node.bus_id}-{self._node.address}")

        return DeviceInfo(
            identifiers={identifier},
            name=custom_name or f"VeloDimmer {self._node.address}",
            manufacturer=self._node.manufacturer,
            model=self._node.model or "VeloDimmer-1",
            sw_version=self._node.sw_version,
            hw_version=self._node.hw_version,
            suggested_area=self._node.suggested_area,
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes."""
        last_encoder = (
            self._last_encoder_time.isoformat() if self._last_encoder_time else None
        )
        return {
            "bus": self._node.bus_id,
            "address": self._node.address,
            "channel": self._ch,
            "last_encoder": last_encoder,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on light."""
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]

        if not self._is_on and ATTR_BRIGHTNESS not in kwargs:
            self._brightness = 255

        await self._hub.async_set_pwm(
            self._node.bus_id, self._node.address, self._ch, self._brightness
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off light."""
        await self._hub.async_set_pwm(
            self._node.bus_id, self._node.address, self._ch, 0
        )

    async def async_added_to_hass(self) -> None:
        """Handle entity added."""

        @callback
        def _on_pwm_change(val: int) -> None:
            self._brightness = max(1, min(255, val))
            self._is_on = self._brightness > 0
            self.async_write_ha_state()

        self._unsub_pwm = self._hub.subscribe_pwm(
            self._node.bus_id, self._node.address, self._ch, _on_pwm_change
        )

        @callback
        def _on_button(pressed: bool) -> None:
            if pressed:
                if self._is_on:
                    self.hass.async_create_task(self.async_turn_off())
                else:
                    self.hass.async_create_task(self.async_turn_on())

        self._unsub_button = self._hub.subscribe_button(
            self._node.bus_id, self._node.address, self._ch, _on_button
        )

        @callback
        def _on_encoder(delta: int) -> None:
            self._last_encoder_time = dt_util.utcnow()

            if not self._is_on:
                self._brightness = 50
                self.hass.async_create_task(self.async_turn_on())
                return

            new_brightness = self._brightness + (delta * 5)
            new_brightness = max(1, min(255, new_brightness))

            self._brightness = new_brightness
            self.hass.async_create_task(
                self._hub.async_set_pwm(
                    self._node.bus_id, self._node.address, self._ch, new_brightness
                )
            )

        self._unsub_encoder = self._hub.subscribe_encoder(
            self._node.bus_id, self._node.address, self._ch, _on_encoder
        )

        # USUNIĘTO: Subskrypcja aktualizacji nazwy
        # @callback
        # def _on_name_update(data: dict) -> None:
        #     ...

        # self._unsub_name_update = async_dispatcher_connect(
        #     self._hass, signal_device_name_updated(self._entry_id), _on_name_update
        # )

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal."""
        if self._unsub_pwm:
            self._unsub_pwm()
        if self._unsub_button:
            self._unsub_button()
        if self._unsub_encoder:
            self._unsub_encoder()
        # USUNIĘTO: Anulowanie subskrypcji nazwy
        # if self._unsub_name_update:
        #     self._unsub_name_update()
        #     self._unsub_name_update = None

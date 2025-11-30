"""Storage management for Velolink."""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION, STORAGE_KEY, POLARITY_NO

_LOGGER = logging.getLogger(__name__)


class VelolinkStorage:
    """Manage persistent storage for Velolink configuration."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize storage."""
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: Dict[str, Any] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Load data from storage."""
        if self._loaded:
            return

        data = await self._store.async_load()
        if data is None:
            data = {"channels": {}, "devices": {}}

        self._data = data
        self._loaded = True
        _LOGGER.info(
            "Velolink storage loaded: %d channels, %d devices",
            len(self._data.get("channels", {})),
            len(self._data.get("devices", {})),
        )

    async def async_save(self) -> None:
        """Save data to storage."""
        await self._store.async_save(self._data)

    def get_channel_config(
        self, bus_id: str, addr: int, ch_type: str, ch: int
    ) -> Dict[str, Any]:
        """Get channel configuration."""
        key = f"{bus_id}-{addr}-{ch_type}-{ch}"
        return self._data.get("channels", {}).get(
            key,
            {
                "device_class": "none",
                "polarity": POLARITY_NO,
            },
        )

    async def async_set_channel_config(
        self,
        bus_id: str,
        addr: int,
        ch_type: str,
        ch: int,
        device_class: str | None = None,
        polarity: str | None = None,
    ) -> None:
        """Set channel configuration."""
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        key = f"{bus_id}-{addr}-{ch_type}-{ch}"
        if "channels" not in self._data:
            self._data["channels"] = {}

        cfg = self._data["channels"].setdefault(key, {})

        if device_class is not None:
            cfg["device_class"] = device_class
        if polarity is not None:
            cfg["polarity"] = polarity

        await self.async_save()
        _LOGGER.info("Updated channel %s: %s", key, cfg)

    def get_device_name(self, bus_id: str, addr: int) -> str | None:
        """Get custom device name."""
        key = f"{bus_id}-{addr}"
        return self._data.get("devices", {}).get(key, {}).get("name")

    # USUNIĘTO: Metoda async_set_device_name
    # async def async_set_device_name(self, bus_id: str, addr: int, name: str) -> None:
    #     """Set custom device name."""
    #     ...

"""Velolink integration."""

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from .hub import VelolinkHub
from .const import CONF_GATEWAY_HOST, CONF_GATEWAY_PORT, DOMAIN, signal_new_node

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SWITCH, Platform.LIGHT, Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_GATEWAY_HOST]
    port = entry.data.get(CONF_GATEWAY_PORT, 5485)
    hub = VelolinkHub(hass, entry.entry_id, host, port)

    try:
        await hub.async_start()
    except Exception as ex:
        _LOGGER.error("Failed to start hub: %s", ex)
        return False

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub: VelolinkHub = hass.data[DOMAIN].pop(entry.entry_id)
    await hub.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

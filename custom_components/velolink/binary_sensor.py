import logging
from homeassistant.core import callback
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN, NODE_KIND_OUTPUT, signal_new_node

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    hub = hass.data[DOMAIN][entry.entry_id]
    created = set()

    @callback
    def _handle_new_node(node):
        if node.kind == NODE_KIND_OUTPUT:
            for ch in range(node.channels):
                uid = f"{node.bus_id}-{node.address}-btn-{ch}"
                if uid in created:
                    continue
                created.add(uid)
                async_add_entities(
                    [VelolinkBtnEntity(hub, entry.entry_id, hub, node, ch)]
                )

    unsub = async_dispatcher_connect(
        hass, signal_new_node(entry.entry_id), _handle_new_node
    )
    entry.async_on_unload(unsub)


class VelolinkBtnEntity(BinarySensorEntity):
    _attr_should_poll = False

    def __init__(self, hub, entry_id, hub_ref, node, ch):
        self._hub = hub_ref
        self._bus_id = node.bus_id
        self._addr = node.address
        self._ch = ch
        self._state = False
        self._unsub = None

    @property
    def unique_id(self):
        return f"{self._bus_id}-{self._addr}-btn-{self._ch}"

    @property
    def name(self):
        return f"Btn {self._ch} [{self._addr}]"

    @property
    def is_on(self):
        return self._state

    async def async_added_to_hass(self):
        @callback
        def _on_change(val):
            self._state = val
            self.async_write_ha_state()

        self._unsub = self._hub.subscribe_input(
            self._bus_id, self._addr, self._ch, _on_change
        )

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()

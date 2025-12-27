import logging
from homeassistant.core import callback
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN, NODE_KIND_OUTPUT, NODE_KIND_PWM, signal_new_node

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

async def async_setup_entry(hass, entry, async_add_entities):
    hub = hass.data[DOMAIN][entry.entry_id]
    created = set()
    @callback
    def _handle_new_node(node):
        if node.kind in (NODE_KIND_OUTPUT, NODE_KIND_PWM):
            uid = f"{node.bus_id}-{node.address}-temp"
            if uid in created: return
            created.add(uid)
            async_add_entities([VelolinkTempEntity(hub, entry.entry_id, hub, node)])
    unsub = async_dispatcher_connect(hass, signal_new_node(entry.entry_id), _handle_new_node)
    entry.async_on_unload(unsub)

class VelolinkTempEntity(SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    def __init__(self, hub, entry_id, hub_ref, node):
        self._hub = hub_ref
        self._bus_id = node.bus_id
        self._addr = node.address
        self._state = None
        self._unsub = None

    @property
    def unique_id(self): return f"{self._bus_id}-{self._addr}-temp"
    @property
    def name(self): return f"Temp [{self._addr}]"
    @property
    def native_value(self): return self._state
    @property
    def native_unit_of_measurement(self): return "°C"

    async def async_added_to_hass(self):
        @callback
        def _on_change(val): self._state = round(val, 1); self.async_write_ha_state()
        self._unsub = self._hub.subscribe_analog(self._bus_id, self._addr, 0, _on_change)

    async def async_will_remove_from_hass(self):
        if self._unsub: self._unsub()
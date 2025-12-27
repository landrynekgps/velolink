import logging
from homeassistant.core import callback
from homeassistant.components.light import LightEntity, ColorMode, ATTR_BRIGHTNESS
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN, NODE_KIND_PWM, signal_new_node

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

async def async_setup_entry(hass, entry, async_add_entities):
    hub = hass.data[DOMAIN][entry.entry_id]
    created = set()
    @callback
    def _handle_new_node(node):
        if node.kind == NODE_KIND_PWM:
            for ch in range(node.channels):
                uid = f"{node.bus_id}-{node.address}-pwm-{ch}"
                if uid in created: continue
                created.add(uid)
                async_add_entities([VelolinkPWMEntity(hub, entry.entry_id, hub, node, ch)])
    unsub = async_dispatcher_connect(hass, signal_new_node(entry.entry_id), _handle_new_node)
    entry.async_on_unload(unsub)

class VelolinkPWMEntity(LightEntity):
    _attr_should_poll = False
    def __init__(self, hub, entry_id, hub_ref, node, ch):
        self._hub = hub_ref
        self._bus_id = node.bus_id
        self._addr = node.address
        self._ch = ch
        self._state = False
        self._brightness = 0
        self._unsub = None

    @property
    def unique_id(self): return f"{self._bus_id}-{self._addr}-pwm-{self._ch}"
    @property
    def name(self): return f"PWM {self._ch} [{self._addr}]"
    @property
    def is_on(self): return self._state
    @property
    def brightness(self): return self._brightness
    @property
    def color_mode(self): return ColorMode.BRIGHTNESS
    @property
    def supported_color_modes(self): return {ColorMode.BRIGHTNESS}

    async def async_turn_on(self, **kwargs):
        self._state = True
        if ATTR_BRIGHTNESS in kwargs: self._brightness = kwargs[ATTR_BRIGHTNESS]
        else: self._brightness = 255
        await self._hub.async_set_pwm(self._bus_id, self._addr, self._ch, self._brightness)
    
    async def async_turn_off(self, **kwargs):
        self._state = False
        await self._hub.async_set_pwm(self._bus_id, self._addr, self._ch, 0)

    async def async_added_to_hass(self):
        @callback
        def _on_change(val): 
            self._brightness = val
            self._state = val > 0
            self.async_write_ha_state()
        self._unsub = self._hub.subscribe_pwm(self._bus_id, self._addr, self._ch, _on_change)

    async def async_will_remove_from_hass(self):
        if self._unsub: self._unsub()
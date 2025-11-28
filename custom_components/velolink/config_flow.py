"""Config flow for Velolink."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_PORT1,
    CONF_PORT2,
    CONF_BAUDRATE,
    CONF_RTS_TOGGLE,
    CONF_SCAN_ON_STARTUP,
    CONF_GATEWAY_HOST,
    CONF_GATEWAY_PORT,
    CONF_CONNECTION_TYPE,
    CONN_TYPE_SERIAL,
    CONN_TYPE_TCP,
    DEFAULT_BAUDRATE,
    DEFAULT_RTS_TOGGLE,
    DEFAULT_SCAN_ON_STARTUP,
    DEFAULT_GATEWAY_PORT,
    DEVICE_CLASS_INPUT_MAP,
    DEVICE_CLASS_OUTPUT_MAP,
    POLARITY_NO,
    POLARITY_NC,
    # Stałe do OptionsFlow
    NODE_KIND_INPUT,
    NODE_KIND_OUTPUT,
    NODE_KIND_VELOSWITCH,
    NODE_KIND_VELOMOTION,
    SERVICE_SET_CHANNEL_CONFIG,
    SERVICE_SET_DEVICE_NAME,
    ATTR_BUS_ID,
    ATTR_ADDRESS,
    ATTR_CHANNEL,
    ATTR_DEVICE_CLASS,
    ATTR_POLARITY,
    ATTR_DEVICE_NAME,
    CONN_TYPE_DEMO,
)
from .hub import VelolinkHub, VelolinkBusConfig
from .storage import VelolinkStorage

_LOGGER = logging.getLogger(__name__)

# Stałe dla nowego wyboru
CONN_CHOICE_RPI_HAT = "rpi_hat"
CONN_CHOICE_USB = "usb"
CONN_CHOICE_TCP = "tcp"
CONN_CHOICE_DEMO = "demo"


def _list_serial_ports() -> dict[str, str]:
    """List and categorize available serial ports."""
    ports = {}
    try:
        from serial.tools import list_ports

        _LOGGER.debug("Scanning for serial ports...")
        for port in list_ports.comports():
            device_path = port.device
            description = f"{port.description} ({device_path})"

            # SZEROKIE wykrywanie RPi HAT - rozszerzone o więcej ścieżek
            if any(x in device_path for x in [
                "ttyAMA", "serial", "ttySC", "ttyS0", 
                "ttyAMA1", "ttyAMA2", "ttyAMA3",  # Dodatkowe porty AMA
                "serial0", "serial1",              # Alternatywne nazwy
                "ttyS1", "ttyS2", "ttyS3"        # Dodatkowe porty S
            ]):
                ports[device_path] = f"Raspberry Pi HAT ({device_path})"
                _LOGGER.info("Detected RPi HAT port: %s", device_path)
            elif "USB" in device_path or "ttyUSB" in device_path or "ttyACM" in device_path:
                ports[device_path] = description
                _LOGGER.info("Detected USB port: %s", device_path)
    except Exception as ex:
        _LOGGER.warning("Failed to list serial ports with pyserial: %s", ex)

    # ZAWSZE dodaj standardowe porty RPi jako fallback, ale sprawdzaj ich istnienie
    default_ports = {
        "/dev/ttyAMA0": "RPi HAT (/dev/ttyAMA0) - Standardowy UART",
        "/dev/ttyAMA1": "RPi HAT (/dev/ttyAMA1) - Dodatkowy UART",
        "/dev/ttySC0": "RPi HAT (/dev/ttySC0) - SC16IS752",
        "/dev/ttySC1": "RPi HAT (/dev/ttySC1) - SC16IS752",
        "/dev/ttyS0": "RPi HAT (/dev/ttyS0) - Mini UART",
        "/dev/ttyS1": "RPi HAT (/dev/ttyS1) - Dodatkowy UART",
        "/dev/serial0": "RPi HAT (/dev/serial0) - Systemowy UART",
        "/dev/serial1": "RPi HAT (/dev/serial1) - Dodatkowy UART",
        "/dev/ttyUSB0": "USB Adapter (/dev/ttyUSB0) - Adapter USB-RS485",
        "/dev/ttyUSB1": "USB Adapter (/dev/ttyUSB1) - Adapter USB-RS485",
        "/dev/ttyACM0": "USB Adapter (/dev/ttyACM0) - Adapter USB-RS485",
    }

    for port, desc in default_ports.items():
        if port not in ports and os.path.exists(port):
            ports[port] = desc
            _LOGGER.debug("Added fallback port: %s", port)
        elif port not in ports:
            _LOGGER.debug("Skipping unavailable port: %s", port)

    _LOGGER.info("Final port list: %s", list(ports.keys()))
    return ports


class VelolinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Velolink."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._connection_type: str | None = None
        self._hat_ports: dict[str, str] | None = None
        self._usb_ports: dict[str, str] | None = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            self._connection_type = user_input["connection_choice"]
            if self._connection_type == CONN_CHOICE_RPI_HAT:
                return await self.async_step_serial_hat()
            if self._connection_type == CONN_CHOICE_USB:
                return await self.async_step_serial_usb()
            if self._connection_type == CONN_CHOICE_TCP:
                return await self.async_step_tcp()
            if self._connection_type == CONN_CHOICE_DEMO:
                return await self.async_step_demo()

        # FIX: ZAWSZE pokazuj wszystkie opcje
        options = {
            CONN_CHOICE_RPI_HAT: "Raspberry Pi HAT (ttyAMA0)",
            CONN_CHOICE_USB: "Adapter USB-RS485",
            CONN_CHOICE_TCP: "TCP (VeloGateway)",
            CONN_CHOICE_DEMO: "Tryb Demo (testowanie bez sprzętu)",
        }

        schema = vol.Schema({vol.Required("connection_choice"): vol.In(options)})
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _create_serial_entry(
        self, user_input: dict[str, Any], title: str, step_id: str
    ) -> FlowResult:
        """Helper to create a serial connection entry."""
        if user_input.get(CONF_PORT2) == "":
            user_input.pop(CONF_PORT2, None)

        port1 = user_input.get(CONF_PORT1)
        port2 = user_input.get(CONF_PORT2)

        if port2 and port1 == port2:
            return self.async_show_form(
                step_id=step_id,
                data_schema=vol.Schema(self._get_serial_schema(step_id, user_input)),
                errors={"base": "ports_identical"},
            )

        user_input[CONF_CONNECTION_TYPE] = CONN_TYPE_SERIAL
        uid = f"serial-{port1}-{port2 or ''}"
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(title=title, data=user_input)

    def _get_serial_schema(self, step_id: str, user_input: dict[str, Any]) -> dict:
        """Get correct schema for current step."""
        base_schema = {
            vol.Required(CONF_BAUDRATE, default=user_input.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)): cv.positive_int,
            vol.Required(CONF_RTS_TOGGLE, default=user_input.get(CONF_RTS_TOGGLE, DEFAULT_RTS_TOGGLE)): bool,
            vol.Required(
                CONF_SCAN_ON_STARTUP, default=user_input.get(CONF_SCAN_ON_STARTUP, DEFAULT_SCAN_ON_STARTUP)
            ): bool,
        }

        if step_id == "serial_hat":
            if self._hat_ports and len(self._hat_ports) > 1:
                base_schema[vol.Required(CONF_PORT1)] = vol.In(self._hat_ports)
        elif step_id == "serial_usb":
            base_schema[vol.Required(CONF_PORT1)] = vol.In(self._usb_ports)
            # TEN FRAGMENT JEST KLUCZOWY - UPENIONIA, ŻE WIDZI DRUGIE POLE WYBORU
            base_schema[vol.Optional(CONF_PORT2)] = vol.In({"": "(brak)"} | self._usb_ports)
        
        return base_schema

    async def async_step_serial_hat(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle RPi HAT serial connection setup."""
        all_ports = await self.hass.async_add_executor_job(_list_serial_ports)
        self._hat_ports = {
            p: d for p, d in all_ports.items() if any(x in p for x in ["ttyAMA", "serial", "ttySC", "ttyS", "serial0", "serial1"])
        }

        if not self._hat_ports:
            _LOGGER.warning("No HAT ports found. Available ports: %s", list(all_ports.keys()))
            return self.async_abort(reason="no_hat_ports_found")

        if user_input is not None:
            # Jeśli port nie był wybrany, użyj pierwszego dostępnego
            selected_port = user_input.get(CONF_PORT1, next(iter(self._hat_ports.keys())))
            _LOGGER.info("Using HAT port: %s", selected_port)
            user_input[CONF_PORT1] = selected_port
            return await self._create_serial_entry(user_input, "Velolink RPi HAT", "serial_hat")

        # Jeśli jest więcej niż jeden port HAT, pozwól użytkownikowi wybrać
        if len(self._hat_ports) > 1:
            schema = vol.Schema({
                vol.Required(CONF_PORT1): vol.In(self._hat_ports),
                vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): cv.positive_int,
                vol.Required(CONF_RTS_TOGGLE, default=DEFAULT_RTS_TOGGLE): bool,
                vol.Required(
                    CONF_SCAN_ON_STARTUP, default=DEFAULT_SCAN_ON_STARTUP
                ): bool,
            })
        else: # Jeśli jest tylko jeden port HAT, użyj go automatycznie
            schema = vol.Schema({
                vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): cv.positive_int,
                vol.Required(CONF_RTS_TOGGLE, default=DEFAULT_RTS_TOGGLE): bool,
                vol.Required(
                    CONF_SCAN_ON_STARTUP, default=DEFAULT_SCAN_ON_STARTUP
                ): bool,
            })

        return self.async_show_form(step_id="serial_hat", data_schema=schema)

    async def async_step_serial_usb(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle USB adapter serial connection setup."""
        all_ports = await self.hass.async_add_executor_job(_list_serial_ports)
        self._usb_ports = {
            p: d for p, d in all_ports.items() if "ttyUSB" in p or "ttyACM" in p
        }

        if not self._usb_ports:
            return self.async_abort(reason="no_usb_ports_found")

        if user_input is not None:
            return await self._create_serial_entry(user_input, f"Velolink USB ({user_input[CONF_PORT1]})", "serial_usb")

        # TEN SCHEMAT JEST KLUCZOWY I POKAZUJE OBA POLA WYBORU
        schema = vol.Schema(
            {
                vol.Required(CONF_PORT1): vol.In(self._usb_ports),
                vol.Optional(CONF_PORT2): vol.In({"": "(brak)"} | self._usb_ports),
                vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): cv.positive_int,
                vol.Required(CONF_RTS_TOGGLE, default=DEFAULT_RTS_TOGGLE): bool,
                vol.Required(
                    CONF_SCAN_ON_STARTUP, default=DEFAULT_SCAN_ON_STARTUP
                ): bool,
            }
        )

        return self.async_show_form(step_id="serial_usb", data_schema=schema)

    async def async_step_tcp(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle TCP connection setup."""
        if user_input is not None:
            user_input[CONF_CONNECTION_TYPE] = CONN_TYPE_TCP
            host = user_input[CONF_GATEWAY_HOST]
            port = user_input[CONF_GATEWAY_PORT]
            uid = f"tcp-{host}-{port}"
            await self.async_set_unique_id(uid)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Velolink Gateway ({host})",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_GATEWAY_HOST): str,
                vol.Required(CONF_GATEWAY_PORT, default=DEFAULT_GATEWAY_PORT): cv.port,
                vol.Required(
                    CONF_SCAN_ON_STARTUP, default=DEFAULT_SCAN_ON_STARTUP
                ): bool,
            }
        )
        return self.async_show_form(step_id="tcp", data_schema=schema)

    async def async_step_demo(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle demo mode setup."""
        user_input = user_input or {}
        user_input[CONF_CONNECTION_TYPE] = CONN_TYPE_DEMO
        uid = "demo-mode"
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Velolink (Tryb Demo)", data=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get options flow handler."""
        return VelolinkOptionsFlow(config_entry)


class VelolinkOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Velolink."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._channel_to_edit: dict[str, Any] | None = None
        self._device_to_edit: dict[str, Any] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "scan_devices": "🔍 Skanuj nowe urządzenia",
                "edit_channel": "⚙️ Edytuj kanał (Device Class, NO/NC)",
                "edit_device_name": "✏️ Zmień nazwę urządzenia",
            },
        )

    async def async_step_scan_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle scanning for new devices."""
        if DOMAIN not in self.hass.data:
            return self.async_abort(reason="integration_not_setup")

        hub: VelolinkHub = self.hass.data[DOMAIN][self._config_entry.entry_id]

        if user_input is not None:
            bus_id = user_input["bus_selection"]
            _LOGGER.info("Options flow: scanning bus %s", bus_id)
            await self.hass.services.async_call(
                DOMAIN,
                f"discovery_{bus_id}",
                blocking=True,
            )
            return self.async_show_form(
                step_id="scan_result",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "result": f"Skanowanie magistrali {bus_id} zakończone. Sprawdź logi, jeśli nowe urządzenia nie pojawiły się."
                },
            )

        buses = list(hub._buses_cfg.keys())
        options = {bus: f"Magistrala {bus.title()}" for bus in buses}
        return self.async_show_form(
            step_id="scan_devices",
            data_schema=vol.Schema({vol.Required("bus_selection"): vol.In(options)}),
        )

    async def async_step_scan_result(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the result of the scan and exit."""
        return self.async_create_entry(title="", data={})

    async def async_step_edit_channel(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle editing a channel configuration."""
        if DOMAIN not in self.hass.data:
            return self.async_abort(reason="integration_not_setup")

        hub: VelolinkHub = self.hass.data[DOMAIN][self._config_entry.entry_id]
        storage: VelolinkStorage = self.hass.data[DOMAIN][
            f"{self._config_entry.entry_id}_storage"
        ]

        channels = {}
        for (bus_id, addr), node in hub._nodes.items():
            ch_type = None
            if node.kind in (
                NODE_KIND_INPUT,
                NODE_KIND_VELOSWITCH,
                NODE_KIND_VELOMOTION,
            ):
                ch_type = "in"
            elif node.kind == NODE_KIND_OUTPUT:
                ch_type = "out"

            if ch_type:
                for ch in range(node.channels):
                    key = f"{bus_id}-{addr}-{ch_type}-{ch}"
                    custom_name = storage.get_device_name(bus_id, addr)
                    name = custom_name or f"Urządzenie {addr}"
                    channels[key] = f"{name} ({ch_type.upper()} {ch}) na {bus_id}"

        if not channels:
            return self.async_abort(reason="no_channels")

        if user_input is not None:
            self._channel_to_edit = user_input["channel"]
            parts = self._channel_to_edit.split("-")
            bus_id, addr, ch_type, ch = parts[0], int(parts[1]), parts[2], int(parts[3])

            device_class_options = {
                **DEVICE_CLASS_INPUT_MAP,
                **DEVICE_CLASS_OUTPUT_MAP,
            }

            current_config = storage.get_channel_config(bus_id, addr, ch_type, ch)

            return self.async_show_form(
                step_id="configure_channel",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            "device_class", default=current_config.get("device_class")
                        ): vol.In(list(device_class_options.keys())),
                        vol.Required(
                            "polarity", default=current_config.get("polarity")
                        ): vol.In([POLARITY_NO, POLARITY_NC]),
                    }
                ),
                description_placeholders={
                    "bus_id": bus_id,
                    "address": addr,
                    "channel": ch,
                    "type": ch_type.upper(),
                },
            )

        return self.async_show_form(
            step_id="edit_channel",
            data_schema=vol.Schema({vol.Required("channel"): vol.In(channels)}),
            description_placeholders={"count": len(channels)},
        )

    async def async_step_configure_channel(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Save the new channel configuration."""
        if user_input is not None and self._channel_to_edit:
            parts = self._channel_to_edit.split("-")
            bus_id, addr, ch_type, ch = parts[0], int(parts[1]), parts[2], int(parts[3])

            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CHANNEL_CONFIG,
                {
                    ATTR_BUS_ID: bus_id,
                    ATTR_ADDRESS: addr,
                    ATTR_CHANNEL: ch,
                    ATTR_DEVICE_CLASS: user_input["device_class"],
                    ATTR_POLARITY: user_input["polarity"],
                },
                blocking=True,
            )
            return self.async_create_entry(title="", data={})

        return self.async_abort(reason="unknown")

    async def async_step_edit_device_name(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle editing a device name."""
        if DOMAIN not in self.hass.data:
            return self.async_abort(reason="integration_not_setup")

        hub: VelolinkHub = self.hass.data[DOMAIN][self._config_entry.entry_id]
        storage: VelolinkStorage = self.hass.data[DOMAIN][
            f"{self._config_entry.entry_id}_storage"
        ]

        devices = {}
        for (bus_id, addr), node in hub._nodes.items():
            key = f"{bus_id}-{addr}"
            custom_name = storage.get_device_name(bus_id, addr)
            name = custom_name or f"Velolink {node.kind.title()} {addr}"
            devices[key] = f"{name} ({bus_id})"

        if not devices:
            return self.async_abort(reason="no_devices")

        if user_input is not None:
            self._device_to_edit = user_input["device"]
            bus_id, addr = self._device_to_edit.split("-")
            current_name = storage.get_device_name(bus_id, addr) or ""

            return self.async_show_form(
                step_id="set_device_name",
                data_schema=vol.Schema(
                    {vol.Required("new_name", default=current_name): str}
                ),
                description_placeholders={
                    "device": devices[self._device_to_edit],
                    "current": current_name,
                },
            )

        return self.async_show_form(
            step_id="edit_device_name",
            data_schema=vol.Schema({vol.Required("device"): vol.In(devices)}),
            description_placeholders={"count": len(devices)},
        )

    async def async_step_set_device_name(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Save the new device name."""
        if user_input is not None and self._device_to_edit:
            bus_id, addr = self._device_to_edit.split("-")

            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_SET_DEVICE_NAME,
                {
                    ATTR_BUS_ID: bus_id,
                    ATTR_ADDRESS: int(addr),
                    ATTR_DEVICE_NAME: user_input["new_name"],
                },
                blocking=True,
            )
            return self.async_create_entry(title="", data={})

        return self.async_abort(reason="unknown")
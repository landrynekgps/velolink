"""Constants for Velolink integration."""

from __future__ import annotations
from enum import IntEnum
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.switch import SwitchDeviceClass

DOMAIN = "velolink"

# Config flow
CONF_PORT1 = "port1"
CONF_PORT2 = "port2"
CONF_BAUDRATE = "baudrate"
CONF_RTS_TOGGLE = "rts_toggle"
CONF_SCAN_ON_STARTUP = "scan_on_startup"
CONF_GATEWAY_HOST = "gateway_host"
CONF_GATEWAY_PORT = "gateway_port"
CONF_CONNECTION_TYPE = "connection_type"

# Connection types
CONN_TYPE_SERIAL = "serial"
CONN_TYPE_TCP = "tcp"
CONN_TYPE_DEMO = "demo"

DEFAULT_BAUDRATE = 115200
DEFAULT_RTS_TOGGLE = False
DEFAULT_SCAN_ON_STARTUP = True
DEFAULT_GATEWAY_PORT = 5485


# Signals
def signal_new_node(entry_id: str) -> str:
    """Signal for new node discovered."""
    return f"{DOMAIN}.{entry_id}.new_node"


def signal_discovery_complete(entry_id: str) -> str:
    """Signal for discovery complete."""
    return f"{DOMAIN}.{entry_id}.discovery_complete"


def signal_channel_config_updated(entry_id: str) -> str:
    """Signal for channel config updated."""
    return f"{DOMAIN}.{entry_id}.config_updated"


def signal_device_name_updated(entry_id: str) -> str:
    """Signal for device name updated."""
    return f"{DOMAIN}.{entry_id}.device_name_updated"


# Intervals
DISCOVERY_INTERVAL_S = 30.0
BUS_POLL_INTERVAL_S = 0.02
GATEWAY_RECONNECT_DELAY_S = 5.0

# Node kinds
NODE_KIND_INPUT = "input"
NODE_KIND_OUTPUT = "output"
NODE_KIND_PWM = "pwm"
NODE_KIND_ANALOG = "analog"
NODE_KIND_VELOSWITCH = "veloswitch"
NODE_KIND_VELODIMMER = "velodimmer"
NODE_KIND_VELOMOTION = "velomotion"
NODE_KIND_VELOSENSOR = "velosensor"

# Device Classes for inputs
DEVICE_CLASS_INPUT_MAP = {
    "none": None,
    "door": BinarySensorDeviceClass.DOOR,
    "garage_door": BinarySensorDeviceClass.GARAGE_DOOR,
    "window": BinarySensorDeviceClass.WINDOW,
    "motion": BinarySensorDeviceClass.MOTION,
    "occupancy": BinarySensorDeviceClass.OCCUPANCY,
    "opening": BinarySensorDeviceClass.OPENING,
    "tamper": BinarySensorDeviceClass.TAMPER,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "moisture": BinarySensorDeviceClass.MOISTURE,
}

# Device Classes for outputs
DEVICE_CLASS_OUTPUT_MAP = {
    "none": None,
    "outlet": SwitchDeviceClass.OUTLET,
    "switch": SwitchDeviceClass.SWITCH,
}

# Polarity
POLARITY_NO = "NO"
POLARITY_NC = "NC"

# Storage
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.storage"

# Services
SERVICE_DISCOVERY_BUS1 = "discovery_bus1"
SERVICE_DISCOVERY_BUS2 = "discovery_bus2"
SERVICE_DISCOVERY_ALL = "discovery_all"
SERVICE_SET_CHANNEL_CONFIG = "set_channel_config"
SERVICE_SET_DEVICE_NAME = "set_device_name"

# Service attributes
ATTR_BUS_ID = "bus_id"
ATTR_ADDRESS = "address"
ATTR_CHANNEL = "channel"
ATTR_DEVICE_CLASS = "device_class"
ATTR_POLARITY = "polarity"
ATTR_DEVICE_NAME = "device_name"


# Protocol - Function Codes
class FunctionCode(IntEnum):
    """RS485 protocol function codes."""

    DISCOVER = 0x01
    HELLO = 0x02
    READ_INPUTS = 0x03
    SET_OUTPUT = 0x10
    SET_PWM = 0x11
    INPUT_CHANGE = 0x20
    OUTPUT_STATE = 0x21
    PWM_STATE = 0x22
    ANALOG_SAMPLE = 0x23
    BUTTON_EVENT = 0x24
    ENCODER_EVENT = 0x25


# Capabilities
CAP_SUPPORTS_CONFIG = 0x01
CAP_PUSH_EVENTS = 0x04

# FIX: Dodano stałe specyficzne dla RPi
RPI_HAT_DETECT_TIMEOUT = 5.0
RPI_HAT_RETRY_COUNT = 3

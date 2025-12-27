"""Constants for Velolink integration."""

from __future__ import annotations
from enum import IntEnum
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.switch import SwitchDeviceClass

DOMAIN = "velolink"

CONF_GATEWAY_HOST = "gateway_host"
CONF_GATEWAY_PORT = "gateway_port"
CONF_CONNECTION_TYPE = "connection_type"

DEFAULT_GATEWAY_PORT = 5485

# Protocol Constants
FRAME_PREAMBLE = bytes([0xAA, 0x55])
TCP_HEADER_MAGIC = bytes([0x56, 0x4C])
TCP_PROTOCOL_VERSION = 0x01

# Signals
def signal_new_node(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.new_node"

# Node kinds
NODE_KIND_INPUT = "input"
NODE_KIND_OUTPUT = "output"
NODE_KIND_PWM = "pwm"
NODE_KIND_ANALOG = "analog"

# Device Classes
DEVICE_CLASS_INPUT_MAP = {
    "none": None,
    "motion": BinarySensorDeviceClass.MOTION,
}

DEVICE_CLASS_OUTPUT_MAP = {
    "none": None,
    "switch": SwitchDeviceClass.SWITCH,
}

# Function Codes
class FunctionCode(IntEnum):
    DISCOVER = 0x01
    HELLO = 0x02
    SET_OUTPUT = 0x10
    SET_PWM = 0x11
    BUTTON_EVENT = 0x24
    ANALOG_SAMPLE = 0x23

# Intervals
GATEWAY_RECONNECT_DELAY_S = 5.0
"""Velolink hub and RS485 transport."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple, List

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    GATEWAY_RECONNECT_DELAY_S,
    FunctionCode,
    signal_new_node,
    signal_discovery_complete,
)

_LOGGER = logging.getLogger(__name__)

BusId = str
Addr = int
Channel = int


# FIX: Dodano timeout konfiguracyjny
DEFAULT_CONNECT_TIMEOUT = 5.0


@dataclass
class VelolinkNode:
    """Velolink device node."""

    # pylint: disable=too-many-instance-attributes
    bus_id: BusId
    address: Addr
    kind: str
    channels: int
    capabilities: int = 0
    sw_version: str | None = None
    hw_version: str | None = None
    manufacturer: str = "Velolink"
    model: str | None = None
    serial_number: str | None = None
    name: str | None = None
    suggested_area: str | None = None


@dataclass
class VelolinkBusConfig:
    """Bus configuration."""

    port: str | None = None
    host: str | None = None
    tcp_port: int = 5485
    baudrate: int = 115200
    rts_toggle: bool = False
    name: str = "RS485"
    transport: str = "serial"


# ========== Serial Transport ==========
class SerialTransport:
    """Serial RS485 transport."""

    def __init__(
        self,
        hass: HomeAssistant,
        bus_id: BusId,
        cfg: VelolinkBusConfig,
        frame_cb: Callable[[BusId, bytes], None],
    ) -> None:
        """Initialize serial transport."""
        self._hass = hass
        self._bus_id = bus_id
        self._cfg = cfg
        self._frame_cb = frame_cb
        self._serial_transport = None
        self._serial_protocol = None
        self._writer_lock = asyncio.Lock()
        # FIX: Dodano reconnect logic
        self._running = False
        self._reconnect_task: asyncio.Task | None = None

    async def async_start(self) -> None:
        """Start serial connection."""
        self._running = True
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def async_stop(self) -> None:
        """Stop serial connection."""
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._serial_transport:
            self._serial_transport.close()
            self._serial_transport = None

    async def _reconnect_loop(self) -> None:
        """Auto-reconnect loop for serial."""
        while self._running:
            try:
                await self._connect()
                # Czekamy na zatrzymanie
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.warning(
                    "Serial %s disconnected: %s, reconnecting in %ds",
                    self._bus_id,
                    err,
                    GATEWAY_RECONNECT_DELAY_S,
                )
                await asyncio.sleep(GATEWAY_RECONNECT_DELAY_S)

     async def _connect(self) -> None:
        """Connect to serial port."""
        # pylint: disable=import-outside-toplevel,import-error
        import serial
        import serial_asyncio

        _LOGGER.info(
            "Starting Serial %s on %s @ %d",
            self._bus_id,
            self._cfg.port,
            self._cfg.baudrate,
        )

        # FIX: Sprawdź, czy port istnieje i jest dostępny
        if not os.path.exists(self._cfg.port):
            raise RuntimeError(f"Serial port {self._cfg.port} does not exist")
        
        # FIX: Sprawdź uprawnienia do portu
        if not os.access(self._cfg.port, os.R_OK | os.W_OK):
            _LOGGER.warning(
                "Permission denied for serial port %s. User may need to be added to dialout group.",
                self._cfg.port
            )

        loop = asyncio.get_running_loop()
        rs485_settings = None

        if self._cfg.rts_toggle:
            try:
                rs485_settings = serial.rs485.RS485Settings(
                    rts_level_for_tx=True, rts_level_for_rx=False
                )
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("RS485Settings not supported")

        # FIX: Przygotuj słownik argumentów i dodaj rs485_mode tylko, gdy jest potrzebny
        connection_args = {
            "url": self._cfg.port,
            "baudrate": self._cfg.baudrate,
            "bytesize": serial.EIGHTBITS,
            "parity": serial.PARITY_NONE,
            "stopbits": serial.STOPBITS_ONE,
            "timeout": 1,
        }

        if rs485_settings is not None:
            connection_args["rs485_mode"] = rs485_settings

        try:
            self._serial_transport, self._serial_protocol = (
                await serial_asyncio.create_serial_connection(
                    loop,
                    lambda: _SerialProtocol(self._hass, self._frame_cb, self._bus_id),
                    **connection_args,  # Użyj słownika argumentów
                )
            )
        except serial.SerialException as e:
            _LOGGER.error("Failed to connect to serial port %s: %s", self._cfg.port, e)
            raise

        _LOGGER.info("Serial %s connected", self._bus_id)

    async def async_write_frame(self, frame: bytes) -> None:
        """Write frame to serial port."""
        if not self._serial_transport:
            raise RuntimeError("Serial not connected")
        async with self._writer_lock:
            self._serial_transport.write(frame)
            await asyncio.sleep(0)


class _SerialProtocol(asyncio.Protocol):
    """Serial protocol with frame extraction."""

    def __init__(
        self,
        hass: HomeAssistant,
        frame_cb: Callable[[BusId, bytes], None],
        bus_id: BusId,
    ) -> None:
        """Initialize protocol."""
        self._hass = hass
        self.frame_cb = frame_cb
        self.bus_id = bus_id
        self.buffer = bytearray()

    def connection_made(self, transport) -> None:
        """Handle connection made."""
        _LOGGER.debug("Serial connection established for %s", self.bus_id)

    def connection_lost(self, exc) -> None:
        """Handle connection lost."""
        _LOGGER.warning("Serial connection lost for %s: %s", self.bus_id, exc)

    def data_received(self, data: bytes) -> None:
        """Handle received data."""
        self.buffer.extend(data)

        while True:
            frame = self._extract_one_frame()
            if not frame:
                break
            self._hass.loop.call_soon_threadsafe(self.frame_cb, self.bus_id, frame)

        if len(self.buffer) > 512:
            _LOGGER.warning("Bus %s: buffer overflow", self.bus_id)
            self.buffer.clear()

    def _extract_one_frame(self) -> Optional[bytes]:
        """Extract one frame from buffer."""
        buf = self.buffer
        pre_1, pre_2 = 0xAA, 0x55

        while len(buf) >= 8:
            if buf[0] != pre_1 or buf[1] != pre_2:
                buf.pop(0)
                continue

            length = buf[5]
            total = 6 + length + 2

            if len(buf) < total:
                return None

            frame = bytes(buf[:total])
            del buf[:total]
            return frame

        return None


# ========== TCP Transport ==========
class TcpTransport:
    """TCP transport for VeloGateway."""

    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        hass: HomeAssistant,
        bus_id: BusId,
        cfg: VelolinkBusConfig,
        frame_cb: Callable[[BusId, bytes], None],
    ) -> None:
        """Initialize TCP transport."""
        self._hass = hass
        self._bus_id = bus_id
        self._cfg = cfg
        self._frame_cb = frame_cb
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._running = False
        self._writer_lock = asyncio.Lock()
        self._connect_timeout = DEFAULT_CONNECT_TIMEOUT  # FIX: Dodano timeout

    async def async_start(self) -> None:
        """Start TCP connection."""
        self._running = True
        self._read_task = asyncio.create_task(self._reconnect_loop())

    async def async_stop(self) -> None:
        """Stop TCP connection."""
        self._running = False
        if self._read_task:
            self._read_task.cancel()
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

    async def _reconnect_loop(self) -> None:
        """Auto-reconnect loop."""
        while self._running:
            try:
                await self._connect()
                await self._read_loop()
            except asyncio.CancelledError:
                break
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.warning(
                    "TCP %s disconnected: %s, reconnecting in %ds",
                    self._bus_id,
                    err,
                    GATEWAY_RECONNECT_DELAY_S,
                )
                await asyncio.sleep(GATEWAY_RECONNECT_DELAY_S)

    async def _connect(self) -> None:
        """Connect to gateway."""
        _LOGGER.info(
            "Connecting to VeloGateway %s:%d (bus=%s)",
            self._cfg.host,
            self._cfg.tcp_port,
            self._bus_id,
        )

        # FIX: Dodano timeout
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._cfg.host, self._cfg.tcp_port),
                timeout=self._connect_timeout,
            )
            _LOGGER.info("Connected to VeloGateway %s", self._bus_id)
        except asyncio.TimeoutError:
            _LOGGER.error(
                "Timeout connecting to %s:%d",
                self._cfg.host,
                self._cfg.tcp_port,
            )
            raise

    async def _read_loop(self) -> None:
        """Read loop for TCP packets."""
        buffer = bytearray()

        while self._running:
            data = await self._reader.read(1024)
            if not data:
                raise ConnectionError("TCP connection closed")

            buffer.extend(data)

            while True:
                packet = self._extract_tcp_packet(buffer)
                if not packet:
                    break

                # Packet: [MAGIC(2)|VER(1)|BUS(1)|LEN(2)|RS485|CRC(2)]
                frame_len = packet[4] | (packet[5] << 8)
                rs485_frame = packet[6 : 6 + frame_len]

                self._hass.loop.call_soon_threadsafe(
                    self._frame_cb, self._bus_id, rs485_frame
                )

    def _extract_tcp_packet(self, buffer: bytearray) -> Optional[bytes]:
        """Extract TCP packet."""
        magic_1, magic_2 = 0x56, 0x4C
        min_len = 8

        while len(buffer) >= min_len:
            if buffer[0] != magic_1 or buffer[1] != magic_2:
                buffer.pop(0)
                continue

            frame_len = buffer[4] | (buffer[5] << 8)
            total_len = 6 + frame_len + 2

            if len(buffer) < total_len:
                return None

            packet = bytes(buffer[:total_len])
            del buffer[:total_len]
            return packet

        return None

    async def async_write_frame(self, frame: bytes) -> None:
        """Write frame over TCP."""
        if not self._writer:
            raise RuntimeError("TCP not connected")

        async with self._writer_lock:
            magic = bytes([0x56, 0x4C])
            version = bytes([0x01])
            bus_byte = bytes([0x01 if self._bus_id == "bus1" else 0x02])
            length = len(frame).to_bytes(2, "little")

            packet_body = magic + version + bus_byte + length + frame
            crc = VelolinkHub._crc16_value(packet_body)  # Use static method
            packet = packet_body + crc.to_bytes(2, "little")

            self._writer.write(packet)
            await self._writer.drain()


# ========== Demo Transport ==========
class DemoTransport:
    """Demo transport for testing without hardware."""

    def __init__(
        self,
        hass: HomeAssistant,
        bus_id: BusId,
        cfg: VelolinkBusConfig,
        frame_cb: Callable[[BusId, bytes], None],
    ) -> None:
        """Initialize demo transport."""
        self._hass = hass
        self._bus_id = bus_id
        self._frame_cb = frame_cb
        self._running = False
        self._simulator_task: asyncio.Task | None = None
        self._cfg = cfg  # FIX: Przechowujemy cfg

    async def async_start(self) -> None:
        """Start demo simulation."""
        _LOGGER.info("Starting Demo %s", self._bus_id)
        self._running = True
        self._simulator_task = asyncio.create_task(self._simulation_loop())

    async def async_stop(self) -> None:
        """Stop demo simulation."""
        self._running = False
        if self._simulator_task:
            self._simulator_task.cancel()
            try:
                await self._simulator_task
            except asyncio.CancelledError:
                pass

    async def async_write_frame(self, frame: bytes) -> None:
        """Pretend to write a frame."""
        func = frame[3]
        _LOGGER.debug(
            "Demo %s: Received command to send func=0x%02X", self._bus_id, func
        )

        if func == FunctionCode.SET_OUTPUT:
            addr = frame[2]
            ch = frame[6]
            val = frame[7]
            resp_payload = bytes([ch, val])
            resp_frame = VelolinkHub._build_frame(
                addr=addr, func=FunctionCode.OUTPUT_STATE, payload=resp_payload
            )
            self._hass.loop.call_soon_threadsafe(
                self._frame_cb, self._bus_id, resp_frame
            )
        elif func == FunctionCode.SET_PWM:
            addr = frame[2]
            ch = frame[6]
            val = frame[7]
            resp_payload = bytes([ch, val])
            resp_frame = VelolinkHub._build_frame(
                addr=addr, func=FunctionCode.PWM_STATE, payload=resp_payload
            )
            self._hass.loop.call_soon_threadsafe(
                self._frame_cb, self._bus_id, resp_frame
            )

    async def _simulation_loop(self) -> None:
        """Main simulation loop."""
        _LOGGER.info(
            "Demo %s: Simulation loop started, waiting before discovery...",
            self._bus_id,
        )
        await asyncio.sleep(5)
        await self._simulate_discovery()
        asyncio.create_task(self._simulate_input_changes())
        asyncio.create_task(self._simulate_analog_changes())

    async def _simulate_discovery(self):
        """Simulate device discovery by sending HELLO frames."""
        _LOGGER.info("Demo %s: Simulating device discovery", self._bus_id)
        demo_devices = [
            {"addr": 5, "kind": "input", "channels": 4, "model": "IO-INPUT-DEMO"},
            {"addr": 10, "kind": "output", "channels": 2, "model": "IO-OUTPUT-DEMO"},
            {"addr": 15, "kind": "pwm", "channels": 1, "model": "IO-PWM-DEMO"},
            {"addr": 20, "kind": "analog", "channels": 1, "model": "IO-ANALOG-DEMO"},
            {
                "addr": 25,
                "kind": "veloswitch",
                "channels": 1,
                "model": "VELOSWITCH-DEMO",
            },
        ]
        for dev in demo_devices:
            kind_map = {
                "input": 0x00,
                "output": 0x01,
                "pwm": 0x02,
                "analog": 0x03,
                "veloswitch": 0x0A,
            }
            kind_code = kind_map.get(dev["kind"], 0xFF)
            payload = bytes(
                [kind_code, dev["channels"], 0x00, 1, 0, 1, 0, 1, len(dev["model"])]
            ) + dev["model"].encode("ascii")
            frame = VelolinkHub._build_frame(
                addr=dev["addr"], func=FunctionCode.HELLO, payload=payload
            )
            _LOGGER.info(
                "Demo %s: Sending HELLO frame for device at address %d",
                self._bus_id,
                dev["addr"],
            )
            self._hass.loop.call_soon_threadsafe(self._frame_cb, self._bus_id, frame)
            await asyncio.sleep(0.5)

    async def _simulate_input_changes(self):
        """Simulate binary sensor state changes."""
        while self._running:
            await asyncio.sleep(10)
            payload = bytes([0, 1])
            frame = VelolinkHub._build_frame(
                addr=5, func=FunctionCode.INPUT_CHANGE, payload=payload
            )
            self._hass.loop.call_soon_threadsafe(self._frame_cb, self._bus_id, frame)
            await asyncio.sleep(5)
            payload = bytes([0, 0])
            frame = VelolinkHub._build_frame(
                addr=5, func=FunctionCode.INPUT_CHANGE, payload=payload
            )
            self._hass.loop.call_soon_threadsafe(self._frame_cb, self._bus_id, frame)

    async def _simulate_analog_changes(self):
        """Simulate analog sensor value changes."""
        value = 1.0
        while self._running:
            await asyncio.sleep(5)
            value += 0.1
            if value > 3.3:
                value = 1.0
            val_mv = int(value * 1000)
            payload = bytes([0, val_mv & 0xFF, (val_mv >> 8) & 0xFF])
            frame = VelolinkHub._build_frame(
                addr=20, func=FunctionCode.ANALOG_SAMPLE, payload=payload
            )
            self._hass.loop.call_soon_threadsafe(self._frame_cb, self._bus_id, frame)


# ========== Main Hub ==========
class VelolinkHub:
    """Velolink hub managing transports and devices."""

    # pylint: disable=too-many-instance-attributes

    def __init__(
        self, hass: HomeAssistant, entry_id: str, buses: Dict[BusId, VelolinkBusConfig]
    ) -> None:
        """Initialize hub."""
        self._hass = hass
        self._entry_id = entry_id
        self._buses_cfg = buses
        self._transports: Dict[
            BusId, SerialTransport | TcpTransport | DemoTransport
        ] = {}
        self._nodes: Dict[Tuple[BusId, Addr], VelolinkNode] = {}

        # Subscriptions
        self._subs_input: Dict[
            Tuple[BusId, Addr, Channel], List[Callable[[bool], None]]
        ] = {}
        self._subs_output: Dict[
            Tuple[BusId, Addr, Channel], List[Callable[[bool], None]]
        ] = {}
        self._subs_pwm: Dict[
            Tuple[BusId, Addr, Channel], List[Callable[[int], None]]
        ] = {}
        self._subs_analog: Dict[
            Tuple[BusId, Addr, Channel], List[Callable[[float], None]]
        ] = {}
        self._subs_button: Dict[
            Tuple[BusId, Addr, Channel], List[Callable[[bool], None]]
        ] = {}
        self._subs_encoder: Dict[
            Tuple[BusId, Addr, Channel], List[Callable[[int], None]]
        ] = {}

        self._running = False

    async def async_start(self, scan_on_startup: bool = True) -> None:
        """Start hub."""
        for bus_id, cfg in self._buses_cfg.items():
            if cfg.transport == "serial":
                transport = SerialTransport(self._hass, bus_id, cfg, self._on_frame)
            elif cfg.transport == "tcp":
                transport = TcpTransport(self._hass, bus_id, cfg, self._on_frame)
            elif cfg.transport == "demo":
                transport = DemoTransport(self._hass, bus_id, cfg, self._on_frame)
            else:
                raise ValueError(f"Unknown transport: {cfg.transport}")

            await transport.async_start()
            self._transports[bus_id] = transport

        self._running = True

        if scan_on_startup:
            await self.async_discovery_all()

    async def async_stop(self) -> None:
        """Stop hub."""
        self._running = False
        await asyncio.gather(
            *(t.async_stop() for t in self._transports.values()), return_exceptions=True
        )
        self._transports.clear()

    async def async_discovery_bus(self, bus_id: BusId) -> None:
        """Discover devices on bus."""
        _LOGGER.info("Discovery on %s", bus_id)
        frame = VelolinkHub._build_frame(
            addr=0x00, func=FunctionCode.DISCOVER, payload=b""
        )
        await self._transports[bus_id].async_write_frame(frame)
        await asyncio.sleep(2.0)
        async_dispatcher_send(
            self._hass, signal_discovery_complete(self._entry_id), bus_id
        )

    async def async_discovery_all(self) -> None:
        """Discover on all buses."""
        for bus_id in list(self._transports.keys()):
            await self.async_discovery_bus(bus_id)

    # Subscribe methods
    def subscribe_input(
        self,
        bus_id: BusId,
        addr: Addr,
        ch: Channel,
        callback_func: Callable[[bool], None],
    ) -> Callable[[], None]:
        """Subscribe to input changes."""
        key = (bus_id, addr, ch)
        self._subs_input.setdefault(key, []).append(callback_func)

        def unsub() -> None:
            lst = self._subs_input.get(key, [])
            if callback_func in lst:
                lst.remove(callback_func)

        return unsub

    def subscribe_output(
        self,
        bus_id: BusId,
        addr: Addr,
        ch: Channel,
        callback_func: Callable[[bool], None],
    ) -> Callable[[], None]:
        """Subscribe to output changes."""
        key = (bus_id, addr, ch)
        self._subs_output.setdefault(key, []).append(callback_func)

        def unsub() -> None:
            lst = self._subs_output.get(key, [])
            if callback_func in lst:
                lst.remove(callback_func)

        return unsub

    def subscribe_pwm(
        self,
        bus_id: BusId,
        addr: Addr,
        ch: Channel,
        callback_func: Callable[[int], None],
    ) -> Callable[[], None]:
        """Subscribe to PWM changes."""
        key = (bus_id, addr, ch)
        self._subs_pwm.setdefault(key, []).append(callback_func)

        def unsub() -> None:
            lst = self._subs_pwm.get(key, [])
            if callback_func in lst:
                lst.remove(callback_func)

        return unsub

    def subscribe_button(
        self,
        bus_id: BusId,
        addr: Addr,
        ch: Channel,
        callback_func: Callable[[bool], None],
    ) -> Callable[[], None]:
        """Subscribe to button events."""
        key = (bus_id, addr, ch)
        self._subs_button.setdefault(key, []).append(callback_func)

        def unsub() -> None:
            lst = self._subs_button.get(key, [])
            if callback_func in lst:
                lst.remove(callback_func)

        return unsub

    def subscribe_encoder(
        self,
        bus_id: BusId,
        addr: Addr,
        ch: Channel,
        callback_func: Callable[[int], None],
    ) -> Callable[[], None]:
        """Subscribe to encoder events."""
        key = (bus_id, addr, ch)
        self._subs_encoder.setdefault(key, []).append(callback_func)

        def unsub() -> None:
            lst = self._subs_encoder.get(key, [])
            if callback_func in lst:
                lst.remove(callback_func)

        return unsub

    def subscribe_analog(
        self,
        bus_id: BusId,
        addr: Addr,
        ch: Channel,
        callback_func: Callable[[float], None],
    ) -> Callable[[], None]:
        """Subscribe to analog changes."""
        key = (bus_id, addr, ch)
        self._subs_analog.setdefault(key, []).append(callback_func)

        def unsub() -> None:
            lst = self._subs_analog.get(key, [])
            if callback_func in lst:
                lst.remove(callback_func)

        return unsub

    # Commands
    async def async_set_output(
        self, bus_id: BusId, addr: Addr, ch: Channel, on: bool
    ) -> None:
        """Set output state."""
        payload = bytes([ch & 0xFF, 1 if on else 0])
        frame = VelolinkHub._build_frame(
            addr=addr, func=FunctionCode.SET_OUTPUT, payload=payload
        )
        await self._transports[bus_id].async_write_frame(frame)

    async def async_set_pwm(
        self, bus_id: BusId, addr: Addr, ch: Channel, value: int
    ) -> None:
        """Set PWM value."""
        value = max(0, min(255, value))
        payload = bytes([ch & 0xFF, value & 0xFF])
        frame = VelolinkHub._build_frame(
            addr=addr, func=FunctionCode.SET_PWM, payload=payload
        )
        await self._transports[bus_id].async_write_frame(frame)

    @callback
    def _on_frame(self, bus_id: BusId, frame: bytes) -> None:
        """Handle received frame."""
        try:
            parsed = self._parse_frame(frame)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug("Parse error on %s: %s", bus_id, err)
            return

        if parsed["type"] == "HELLO":
            node = VelolinkNode(
                bus_id=bus_id,
                address=parsed["addr"],
                kind=parsed["kind"],
                channels=parsed["channels"],
                capabilities=parsed.get("capabilities", 0),
                sw_version=parsed.get("sw_version"),
                hw_version=parsed.get("hw_version"),
                model=parsed.get("model"),
                serial_number=parsed.get("serial_number"),
                suggested_area=parsed.get("area"),
            )
            self._register_node(node)

        elif parsed["type"] == "INPUT_CHANGE":
            self._emit(
                self._subs_input,
                (bus_id, parsed["addr"], parsed["ch"]),
                bool(parsed["value"]),
            )
        elif parsed["type"] == "OUTPUT_STATE":
            self._emit(
                self._subs_output,
                (bus_id, parsed["addr"], parsed["ch"]),
                bool(parsed["value"]),
            )
        elif parsed["type"] == "PWM_STATE":
            self._emit(
                self._subs_pwm,
                (bus_id, parsed["addr"], parsed["ch"]),
                int(parsed["value"]),
            )
        elif parsed["type"] == "ANALOG_SAMPLE":
            self._emit(
                self._subs_analog,
                (bus_id, parsed["addr"], parsed["ch"]),
                float(parsed["value"]),
            )
        elif parsed["type"] == "BUTTON_EVENT":
            self._emit(
                self._subs_button,
                (bus_id, parsed["addr"], parsed["ch"]),
                bool(parsed["value"]),
            )
        elif parsed["type"] == "ENCODER_EVENT":
            self._emit(
                self._subs_encoder,
                (bus_id, parsed["addr"], parsed["ch"]),
                int(parsed["value"]),
            )

    def _emit(self, subs, key, value):
        """Emit event to subscribers."""
        if key in subs:
            for cb in subs[key]:
                self._hass.loop.call_soon_threadsafe(cb, value)

    def _register_node(self, node: VelolinkNode) -> None:
        """Register new node."""
        key = (node.bus_id, node.address)
        if key not in self._nodes:
            self._nodes[key] = node
            _LOGGER.info("New node discovered: %s", node)
            async_dispatcher_send(self._hass, signal_new_node(self._entry_id), node)

    def get_node(self, bus_id: BusId, addr: Addr) -> VelolinkNode | None:
        """Get node by address."""
        return self._nodes.get((bus_id, addr))

    @staticmethod
    def _build_frame(addr: int, func: int, payload: bytes) -> bytes:
        """Build RS485 frame."""
        # Frame: [PRE(2)|ADDR(1)|FUNC(1)|RES(1)|LEN(1)|PAYLOAD(N)|CRC(2)]
        pre = bytes([0xAA, 0x55])
        header = bytes([addr, func, 0x00, len(payload)])
        body = header + payload
        crc = VelolinkHub._crc16_value(body)
        return pre + body + crc.to_bytes(2, "little")

    @staticmethod
    def _parse_frame(frame: bytes) -> dict:
        """Parse RS485 frame."""
        # Simple parser for demo
        addr = frame[2]
        func = frame[3]
        length = frame[5]
        payload = frame[6 : 6 + length]

        if func == FunctionCode.HELLO:
            # Payload: [KIND(1)|CH(1)|CAP(2)|SW(2)|HW(2)|LEN_M(1)|MODEL(N)...]
            kind_map = {
                0x00: "input",
                0x01: "output",
                0x02: "pwm",
                0x03: "analog",
                0x0A: "veloswitch",
            }
            kind_code = payload[0]
            channels = payload[1]
            model_len = payload[8]
            model = payload[9 : 9 + model_len].decode("ascii", errors="ignore")

            return {
                "type": "HELLO",
                "addr": addr,
                "kind": kind_map.get(kind_code, "unknown"),
                "channels": channels,
                "model": model,
            }
        elif func == FunctionCode.INPUT_CHANGE:
            return {
                "type": "INPUT_CHANGE",
                "addr": addr,
                "ch": payload[0],
                "value": payload[1],
            }
        elif func == FunctionCode.OUTPUT_STATE:
            return {
                "type": "OUTPUT_STATE",
                "addr": addr,
                "ch": payload[0],
                "value": payload[1],
            }
        elif func == FunctionCode.PWM_STATE:
            return {
                "type": "PWM_STATE",
                "addr": addr,
                "ch": payload[0],
                "value": payload[1],
            }
        elif func == FunctionCode.ANALOG_SAMPLE:
            val = payload[1] | (payload[2] << 8)
            return {
                "type": "ANALOG_SAMPLE",
                "addr": addr,
                "ch": payload[0],
                "value": val / 1000.0,
            }

        return {"type": "UNKNOWN"}

    @staticmethod
    def _crc16_value(data: bytes) -> int:
        """Calculate CRC16."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

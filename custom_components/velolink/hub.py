"""Velolink hub and RS485 transport."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, Tuple, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    GATEWAY_RECONNECT_DELAY_S,
    FunctionCode,
    signal_new_node,
    FRAME_PREAMBLE,
    TCP_HEADER_MAGIC,
    TCP_PROTOCOL_VERSION,
)

_LOGGER = logging.getLogger(__name__)

BusId = str
Addr = int
Channel = int
DEFAULT_CONNECT_TIMEOUT = 5.0


class VelolinkNode:
    """Velolink device node."""

    bus_id: BusId
    address: Addr
    kind: str
    channels: int
    sw_version: str | None = None
    hw_version: str | None = None
    model: str | None = None
    serial_number: str | None = None
    name: str | None = None

    def __init__(
        self,
        bus_id: BusId,
        address: Addr,
        kind: str,
        channels: int,
        model: str = "Unknown",
    ):
        self.bus_id = bus_id
        self.address = address
        self.kind = kind
        self.channels = channels
        self.model = model


class TcpGateway:
    """TCP Gateway handling both W5500 and WiFi."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        frame_cb: Callable[[BusId, bytes], None],
    ) -> None:
        self._hass = hass
        self._host = host
        self._port = port
        self._frame_cb = frame_cb
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._running = False
        self._writer_lock = asyncio.Lock()
        self._connect_timeout = DEFAULT_CONNECT_TIMEOUT

    async def async_start(self) -> None:
        self._running = True
        self._read_task = asyncio.create_task(self._reconnect_loop())

    async def async_stop(self) -> None:
        self._running = False
        if self._read_task:
            self._read_task.cancel()
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

    async def _reconnect_loop(self) -> None:
        while self._running:
            try:
                await self._connect()
                await self._read_loop()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.warning("TCP Gateway disconnected: %s", err)
                await asyncio.sleep(GATEWAY_RECONNECT_DELAY_S)

    async def _connect(self) -> None:
        _LOGGER.info("Connecting to %s:%d", self._host, self._port)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._connect_timeout,
            )
            _LOGGER.info("Connected to %s", self._host)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout connecting to %s:%d", self._host, self._port)
            raise

    async def _read_loop(self) -> None:
        buffer = bytearray()
        while self._running:
            data = await self._reader.read(1024)
            if not data:
                raise ConnectionError("TCP closed")
            buffer.extend(data)
            while True:
                packet = self._extract_tcp_packet(buffer)
                if not packet:
                    break
                self._process_tcp_packet(packet)

    def _extract_tcp_packet(self, buffer: bytearray) -> Optional[bytes]:
        magic_len = len(TCP_HEADER_MAGIC)
        min_len = 8
        while len(buffer) >= min_len:
            if buffer[0:magic_len] != TCP_HEADER_MAGIC:
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

    def _process_tcp_packet(self, packet: bytes) -> None:
        magic_len = len(TCP_HEADER_MAGIC)
        if packet[0:magic_len] != TCP_HEADER_MAGIC:
            return
        if packet[magic_len] != TCP_PROTOCOL_VERSION:
            return

        bus_byte = packet[magic_len + 1]
        # Mapowanie busa z protokołu na ID w Pythonie
        # 0x01 = RS485 Bus 1 (w bramce)
        # 0x02 = RS485 Bus 2 (w bramce)
        # 0x03 = WiFi Devices (Virtual Bus w bramce)

        if bus_byte == 0x01:
            bus_id_str = "bus1"
        elif bus_byte == 0x02:
            bus_id_str = "bus2"
        elif bus_byte == 0x03:
            bus_id_str = "bus_wifi"
        else:
            return

        frame_len = packet[magic_len + 2] | (packet[magic_len + 3] << 8)
        header_size = 6
        rs485_frame = packet[header_size : header_size + frame_len]

        self._hass.loop.call_soon_threadsafe(self._frame_cb, bus_id_str, rs485_frame)

    async def async_write_frame(self, bus_id: BusId, frame: bytes) -> None:
        if not self._writer:
            raise RuntimeError("TCP not connected")
        async with self._writer_lock:
            magic = TCP_HEADER_MAGIC
            version = bytes([TCP_PROTOCOL_VERSION])

            # Mapowanie Python ID na bajt protokołu
            if bus_id == "bus1":
                bus_byte = bytes([0x01])
            elif bus_id == "bus2":
                bus_byte = bytes([0x02])
            elif bus_id == "bus_wifi":
                bus_byte = bytes([0x03])
            else:
                raise ValueError("Unknown Bus ID")

            length = len(frame).to_bytes(2, "little")
            packet_body = magic + version + bus_byte + length + frame
            crc = self._crc16(packet_body)
            packet = packet_body + crc.to_bytes(2, "little")
            self._writer.write(packet)
            await self._writer.drain()

    @staticmethod
    def _crc16(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        return crc


class VelolinkHub:
    def __init__(self, hass: HomeAssistant, entry_id: str, host: str, port: int):
        self._hass = hass
        self._entry_id = entry_id
        self._gateway = TcpGateway(hass, host, port, self._on_frame)
        self._nodes: Dict[Tuple[BusId, Addr], VelolinkNode] = {}

        self._subs_input: Dict[Tuple[BusId, Addr, Channel], list[Callable]] = {}
        self._subs_output: Dict[Tuple[BusId, Addr, Channel], list[Callable]] = {}
        self._subs_pwm: Dict[Tuple[BusId, Addr, Channel], list[Callable]] = {}
        self._subs_analog: Dict[Tuple[BusId, Addr, Channel], list[Callable]] = {}

    async def async_start(self) -> None:
        await self._gateway.async_start()
        await self.async_discovery_all()

    async def async_stop(self) -> None:
        await self._gateway.async_stop()

    async def async_discovery_bus(self, bus_id: BusId) -> None:
        _LOGGER.info("Discovery on %s", bus_id)
        frame = self._build_frame(0x00, FunctionCode.DISCOVER, b"")
        await self._gateway.async_write_frame(bus_id, frame)
        await asyncio.sleep(2.0)

    async def async_discovery_all(self) -> None:
        # Szukamy na obu magistralach RS485 i szynie WiFi (0x03)
        await self.async_discovery_bus("bus1")
        await self.async_discovery_bus("bus2")
        await self.async_discovery_bus("bus_wifi")

    async def async_set_output(
        self, bus_id: BusId, addr: Addr, ch: Channel, on: bool
    ) -> None:
        payload = bytes([ch & 0xFF, 1 if on else 0])
        frame = self._build_frame(addr, FunctionCode.SET_OUTPUT, payload)
        await self._gateway.async_write_frame(bus_id, frame)

    async def async_set_pwm(
        self, bus_id: BusId, addr: Addr, ch: Channel, value: int
    ) -> None:
        payload = bytes([ch & 0xFF, value & 0xFF])
        frame = self._build_frame(addr, FunctionCode.SET_PWM, payload)
        await self._gateway.async_write_frame(bus_id, frame)

    @callback
    def _on_frame(self, bus_id: BusId, frame: bytes) -> None:
        try:
            parsed = self._parse_frame(frame)
        except Exception as err:
            return

        if parsed["type"] == "HELLO":
            node = VelolinkNode(
                bus_id=bus_id,
                address=parsed["addr"],
                kind=parsed["kind"],
                channels=parsed["channels"],
                model=parsed.get("model", "Unknown"),
            )
            self._register_node(node)
        elif parsed["type"] == "BUTTON_EVENT":
            self._emit(
                self._subs_input,
                (bus_id, parsed["addr"], parsed["ch"]),
                bool(parsed["value"]),
            )
        elif parsed["type"] == "ANALOG_SAMPLE":
            self._emit(
                self._subs_analog,
                (bus_id, parsed["addr"], parsed["ch"]),
                float(parsed["value"]),
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

    def _emit(self, bucket, key, val) -> None:
        for cb in bucket.get(key, []):
            try:
                cb(val)
            except Exception:
                pass

    def _register_node(self, node: VelolinkNode) -> None:
        key = (node.bus_id, node.address)
        if key not in self._nodes:
            self._nodes[key] = node
            _LOGGER.info("New node: %s", node.model)
            async_dispatcher_send(self._hass, signal_new_node(self._entry_id), node)

    def get_node(self, bus_id: BusId, addr: Addr) -> VelolinkNode | None:
        return self._nodes.get((bus_id, addr))

    def subscribe_input(self, bus_id, addr, ch, cb):
        return self._add_sub(self._subs_input, bus_id, addr, ch, cb)

    def subscribe_output(self, bus_id, addr, ch, cb):
        return self._add_sub(self._subs_output, bus_id, addr, ch, cb)

    def subscribe_pwm(self, bus_id, addr, ch, cb):
        return self._add_sub(self._subs_pwm, bus_id, addr, ch, cb)

    def subscribe_analog(self, bus_id, addr, ch, cb):
        return self._add_sub(self._subs_analog, bus_id, addr, ch, cb)

    def _add_sub(self, bucket, bus_id, addr, ch, cb):
        key = (bus_id, addr, ch)
        bucket.setdefault(key, []).append(cb)

        def unsub():
            lst = bucket.get(key, [])
            lst.remove(cb) if cb in lst else None

        return unsub

    @staticmethod
    def _build_frame(addr: int, func: int, payload: bytes) -> bytes:
        pre = FRAME_PREAMBLE
        seq = 0
        length = len(payload)
        body = bytes([addr & 0xFF, func & 0xFF, seq & 0xFF, length & 0xFF]) + payload
        crc = TcpGateway._crc16(body)
        return pre + body + crc.to_bytes(2, "little")

    def _parse_frame(self, frame: bytes) -> dict:
        pre_len = len(FRAME_PREAMBLE)
        if len(frame) < 8 or frame[0:pre_len] != FRAME_PREAMBLE:
            raise ValueError("bad preamble")

        length = frame[5]
        if len(frame) != 6 + length + 2:
            raise ValueError("length mismatch")

        body = frame[2:-2]
        crc_recv = frame[-2] | (frame[-1] << 8)
        crc_calc = TcpGateway._crc16(body)
        if crc_recv != crc_calc:
            raise ValueError("CRC error")

        addr = frame[2]
        func = frame[3]
        payload = frame[6 : 6 + length]

        if func == FunctionCode.HELLO:
            kind_code = payload[0]
            kind = {0x01: "output", 0x02: "pwm"}.get(kind_code, "unknown")
            channels = payload[1]
            model = (
                payload[8 : 8 + payload[7]].decode("ascii")
                if len(payload) > 8
                else "Unknown"
            )
            return {
                "type": "HELLO",
                "addr": addr,
                "kind": kind,
                "channels": channels,
                "model": model,
            }

        if func == FunctionCode.BUTTON_EVENT:
            return {
                "type": "BUTTON_EVENT",
                "addr": addr,
                "ch": payload[0],
                "value": payload[1],
            }
        if func == FunctionCode.ANALOG_SAMPLE:
            val = payload[1] | (payload[2] << 8) if len(payload) >= 3 else 0
            return {
                "type": "ANALOG_SAMPLE",
                "addr": addr,
                "ch": payload[0],
                "value": val / 1000.0,
            }
        if func == FunctionCode.SET_OUTPUT:
            return {
                "type": "OUTPUT_STATE",
                "addr": addr,
                "ch": payload[0],
                "value": payload[1],
            }
        if func == FunctionCode.SET_PWM:
            return {
                "type": "PWM_STATE",
                "addr": addr,
                "ch": payload[0],
                "value": payload[1],
            }

        raise ValueError(f"unknown func: {func:02X}")

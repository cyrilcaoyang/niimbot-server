import asyncio
import contextlib
from collections.abc import Callable

from .models import Settings
from .protocol import (
    Decoder,
    Packet,
    ProtocolError,
    dimension_payload,
    parse_status,
    raster_rows,
    start_payload,
)


class BlePrinter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        self.characteristic = None
        self.decoder = Decoder()
        self.pending = None
        self.expected = None
        self.fault = None

    @property
    def connected(self):
        return bool(self.client and self.client.is_connected and self.characteristic)

    async def scan(self):
        from bleak import BleakScanner

        devices = await BleakScanner.discover(timeout=5)
        return [
            {"name": d.name, "address": d.address}
            for d in devices
            if d.name and d.name.upper().startswith("D110_M")
        ]

    def disconnected(self, _client):
        self.fault = ConnectionError("Printer disconnected; no automatic reconnect")
        if self.pending and not self.pending.done():
            self.pending.set_exception(self.fault)

    def notification(self, _sender, data):
        try:
            for packet in self.decoder.feed(data):
                if packet.command in (0x00, 0xDB):
                    raise ProtocolError(f"Printer refused command: {packet.data.hex()}")
                if self.pending and not self.pending.done() and packet.command == self.expected:
                    self.pending.set_result(packet.data)
        except ProtocolError as exc:
            self.fault = exc
            if self.pending and not self.pending.done():
                self.pending.set_exception(exc)

    async def connect(self):
        from bleak import BleakClient, BleakScanner

        if not self.settings.target:
            raise ValueError("Set target to the exact D110_M name or macOS Bluetooth UUID")
        devices = await BleakScanner.discover(timeout=5)
        matches = [
            d
            for d in devices
            if d.address.casefold() == self.settings.target.casefold()
            or d.name == self.settings.target
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one matching printer; found {len(matches)}")
        device = matches[0]
        if not device.name or not device.name.upper().startswith("D110_M"):
            raise ValueError("Selected device does not advertise as D110_M")
        self.decoder = Decoder()
        self.fault = None
        self.client = BleakClient(device, disconnected_callback=self.disconnected)
        try:
            await self.client.connect()  # Bleak >=1 returns None, not a success boolean.
            if not self.client.is_connected:
                raise ConnectionError("Bluetooth connection did not become active")
            candidates = [
                c
                for s in self.client.services
                for c in s.characteristics
                if "notify" in c.properties
                and "write-without-response" in c.properties
                and (
                    not self.settings.characteristic
                    or c.uuid.casefold() == self.settings.characteristic.casefold()
                )
            ]
            if len(candidates) != 1:
                raise ProtocolError("GATT characteristic is ambiguous; configure its UUID")
            self.characteristic = candidates[0]
            await self.client.start_notify(self.characteristic, self.notification)
        except BaseException:
            with contextlib.suppress(Exception):
                await self.disconnect()
            raise

    async def disconnect(self):
        try:
            if self.client and self.client.is_connected:
                await self.client.disconnect()
        finally:
            self.characteristic = None

    async def send(self, packet: Packet, guard: Callable[[], None]):
        guard()
        if self.fault:
            raise self.fault
        if not self.connected:
            raise ConnectionError("Printer is not connected")
        data = packet.encode()
        size = self.characteristic.max_write_without_response_size
        for offset in range(0, len(data), size):
            guard()
            await self.client.write_gatt_char(
                self.characteristic, data[offset : offset + size], response=False
            )

    async def command(self, command: int, data: bytes, response: int, guard):
        self.expected = response
        self.pending = asyncio.get_running_loop().create_future()
        try:
            async with asyncio.timeout(self.settings.command_timeout_s):
                await self.send(Packet(command, data), guard)
                return await self.pending
        finally:
            if self.pending and not self.pending.done():
                self.pending.cancel()
            self.pending = None
            self.expected = None

    async def ack(self, command, data, response, guard):
        answer = await self.command(command, data, response, guard)
        if not answer or answer[0] != 1:
            raise ProtocolError(f"Command {command:#x} was not acknowledged: {answer.hex()}")

    async def print_image(self, image, copies, density, guard, on_running):
        profile = self.settings.profile
        await self.ack(0x21, bytes((density,)), 0x31, guard)
        await self.ack(0x23, b"\x01", 0x33, guard)
        await self.ack(0x01, start_payload(copies, profile), 0x02, guard)
        if profile == "d110_m_v4":
            await self.send(Packet(0xA3, b"\x01"), guard)
        else:
            await self.ack(0x03, b"\x01", 0x04, guard)
        await self.ack(0x13, dimension_payload(image.height, copies, profile), 0x14, guard)
        for row in raster_rows(image):
            await self.send(row, guard)
            await asyncio.sleep(self.settings.row_delay_s)
        await self.ack(0xE3, b"\x01", 0xE4, guard)
        # Acknowledging bytes or waiting two seconds is not proof of a printed label.
        while True:
            data = await self.command(0xA3, b"\x01", 0xB3, guard)
            page, progress, feed = parse_status(data)
            if page > copies:
                raise ProtocolError("Printer page count exceeds requested copies")
            if page == copies:
                break
            if progress or feed:
                on_running()
            await asyncio.sleep(0.3)
        if profile == "d110_m_v4":
            await self.ack(0xF3, b"\x01", 0xF4, guard)


class SimulatedPrinter:
    """No Bleak import, scan, socket or hardware access in simulation."""

    connected = False

    async def scan(self):
        return [{"name": "D110_M-SIMULATED", "address": "simulation"}]

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def print_image(self, image, copies, density, guard, on_running):
        guard()
        on_running()
        await asyncio.sleep(0.01)
        guard()

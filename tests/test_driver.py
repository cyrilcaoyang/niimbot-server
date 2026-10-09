import asyncio
import struct
import sys
from types import SimpleNamespace

import pytest
from test_protocol import png

from niimbot_server.driver import BlePrinter
from niimbot_server.models import Settings
from niimbot_server.protocol import Decoder, Packet, ProtocolError, decode_png


class FakeClient:
    instances = []
    omit_response = False

    def __init__(self, device, disconnected_callback):
        self.device = device
        self.is_connected = False
        self.disconnected_callback = disconnected_callback
        self.char = SimpleNamespace(
            uuid="char",
            properties=["notify", "write-without-response"],
            max_write_without_response_size=20,
        )
        self.services = [SimpleNamespace(characteristics=[self.char])]
        self.decoder = Decoder()
        self.sent = []
        self.instances.append(self)

    async def connect(self):
        self.is_connected = True
        return None

    async def disconnect(self):
        self.is_connected = False
        self.disconnected_callback(self)

    async def start_notify(self, char, callback):
        self.callback = callback

    async def write_gatt_char(self, char, data, response):
        assert response is False
        assert len(data) <= 20
        for packet in self.decoder.feed(data):
            self.sent.append(packet)
            replies = {
                0x21: 0x31,
                0x23: 0x33,
                0x01: 0x02,
                0x03: 0x04,
                0x13: 0x14,
                0xE3: 0xE4,
                0xA3: 0xB3,
                0xF3: 0xF4,
            }
            if packet.command in replies and not self.omit_response:
                answer = (
                    struct.pack(">HBB", 1, 100, 100) + bytes(4)
                    if packet.command == 0xA3
                    else b"\x01"
                )
                wire = Packet(replies[packet.command], answer).encode()
                # Wrong response type must not satisfy the pending request.
                self.callback(None, Packet(0x49, b"\x04\x11").encode())
                self.callback(None, wire[:3])
                self.callback(None, wire[3:])


@pytest.fixture
def fake_bleak(monkeypatch):
    class Scanner:
        @staticmethod
        async def discover(timeout):
            return [SimpleNamespace(name="D110_M-test", address="uuid")]

    FakeClient.instances.clear()
    monkeypatch.setitem(
        sys.modules, "bleak", SimpleNamespace(BleakClient=FakeClient, BleakScanner=Scanner)
    )


@pytest.mark.parametrize("profile", ["d110_m", "d110_m_v4", "b1"])
async def test_bleak_none_return_and_protocol_sequence(fake_bleak, profile):
    printer = BlePrinter(Settings(target="uuid", profile=profile, row_delay_s=0))
    if profile == "b1":

        async def discover(timeout):
            return [SimpleNamespace(name="B1-test", address="uuid")]

        sys.modules["bleak"].BleakScanner.discover = discover
    await printer.connect()
    assert printer.connected
    await printer.print_image(decode_png(png(height=2)), 1, 3, lambda: None, lambda: None)
    packets = printer.client.sent
    assert packets[0] == Packet(0x21, b"\x03")
    assert packets[2].command == 0x01
    assert len(packets[2].data) == (9 if profile == "d110_m_v4" else 7)
    assert sum(p.command == 0x85 for p in packets) == 2
    assert not any(p.command == 0x15 for p in packets)
    assert any(p.command == 0xA3 for p in packets)
    if profile == "b1":
        assert packets[-1].command == 0xF3
        assert [p.data for p in packets if p.command == 0x13] == [bytes.fromhex("000201800001")]
        assert all(len(p.data) == 54 for p in packets if p.command == 0x85)
    await printer.disconnect()


async def test_timeout_and_disconnect_fail_instead_of_reconnect(fake_bleak):
    printer = BlePrinter(Settings(target="uuid", command_timeout_s=0.1))
    await printer.connect()
    printer.client.omit_response = True
    with pytest.raises(TimeoutError):
        await printer.command(0x21, b"\x03", 0x31, lambda: None)
    await printer.disconnect()
    with pytest.raises(ConnectionError):
        await printer.send(Packet(0x21, b"\x03"), lambda: None)
    assert len(FakeClient.instances) == 1


async def test_corrupt_notification_fails_pending_command(fake_bleak):
    printer = BlePrinter(Settings(target="uuid"))
    await printer.connect()
    printer.client.omit_response = True
    task = asyncio.create_task(printer.command(0x21, b"\x03", 0x31, lambda: None))
    await asyncio.sleep(0)
    printer.notification(None, bytes.fromhex("555531010100aaaa"))
    with pytest.raises(ProtocolError):
        await task
    await printer.disconnect()


async def test_ambiguous_identity_refused_before_connection(fake_bleak, monkeypatch):
    async def discover(timeout):
        return [SimpleNamespace(name="D110_M-test", address=x) for x in ("one", "two")]

    monkeypatch.setattr(sys.modules["bleak"].BleakScanner, "discover", discover)
    printer = BlePrinter(Settings(target="D110_M-test"))
    with pytest.raises(ValueError, match="found 2"):
        await printer.connect()
    assert not FakeClient.instances


def test_b1_identity_does_not_accept_other_models():
    printer = BlePrinter(Settings(profile="b1"))
    assert printer.settings.equipment_id == "niimbot_b1"
    assert printer.matches_model("B1-example")
    for name in (None, "B18", "B1_PRO-example", "B1_SE-example", "D110_M-example"):
        assert not printer.matches_model(name)


async def test_unsupported_label_queries_do_not_print_or_fault(fake_bleak):
    printer = BlePrinter(Settings(target="uuid"))
    await printer.connect()

    async def reject(char, data, response):
        for packet in printer.client.decoder.feed(data):
            printer.client.sent.append(packet)
            printer.notification(None, Packet(0x00, bytes([packet.command])).encode())

    printer.client.write_gatt_char = reject
    result = await printer.label_info(lambda: None)
    assert result.rfid.status == result.paper.status == "unsupported"
    assert printer.fault is None and printer.connected
    assert [p.command for p in printer.client.sent] == [0x1A, 0x59]
    await printer.disconnect()

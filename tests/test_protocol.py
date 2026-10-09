import base64
import io
import struct

import pytest
from PIL import Image

from niimbot_server.protocol import (
    Decoder,
    Packet,
    ProtocolError,
    decode_png,
    dimension_payload,
    parse_status,
    raster_rows,
    start_payload,
)


def png(width=96, height=8, color="white"):
    image = Image.new("RGBA", (width, height), color)
    data = io.BytesIO()
    image.save(data, format="PNG")
    return base64.b64encode(data.getvalue()).decode()


def test_known_frame_and_fragmented_notifications():
    wire = bytes.fromhex("555531010131aaaa")
    assert Packet(0x31, b"\x01").encode() == wire
    decoder = Decoder()
    assert decoder.feed(wire[:3]) == []
    assert decoder.feed(wire[3:] + wire) == [Packet(0x31, b"\x01")] * 2


@pytest.mark.parametrize("wire", ["005531010131aaaa", "555531010100aaaa", "555531010131aabb"])
def test_corrupt_frame_is_rejected(wire):
    with pytest.raises(ProtocolError):
        Decoder().feed(bytes.fromhex(wire))


def test_d110m_payloads():
    assert start_payload(2, "d110_m").hex() == "00020000000000"
    assert dimension_payload(240, 2, "d110_m").hex() == "00f000600002"
    assert start_payload(2, "d110_m_v4").hex() == "000200000000000000"
    assert dimension_payload(240, 2, "d110_m_v4").hex() == "00f00060000200000000000000"


@pytest.mark.parametrize("length", [4, 8, 10])
def test_extended_status(length):
    assert parse_status(struct.pack(">HBB", 2, 100, 100) + bytes(length - 4)) == (2, 100, 100)


def test_status_error_is_not_completion():
    with pytest.raises(ProtocolError):
        parse_status(bytes.fromhex("00016464000001000000"))
    with pytest.raises(ProtocolError):
        parse_status(b"\x01")


def test_raster_left_alignment_and_transparency():
    image = decode_png(png(9, 1, "black"))
    row = list(raster_rows(image))[0]
    assert row.data[:6] == bytes.fromhex("000000000001")
    assert row.data[6:] == b"\xff\x80" + bytes(10)
    assert list(raster_rows(decode_png(png(1, 1, (0, 0, 0, 0)))))[0].data[6:] == bytes(12)


@pytest.mark.parametrize("encoded", ["not base64!", png(97, 2), png(1, 2401)])
def test_reject_invalid_or_oversized_image(encoded):
    with pytest.raises(ValueError):
        decode_png(encoded)

"""NiimPrintX-derived framing/raster encoding; see THIRD_PARTY.md for provenance."""

import base64
import binascii
import io
import struct
import warnings
from dataclasses import dataclass
from functools import reduce
from operator import xor

from PIL import Image


class ProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Packet:
    command: int
    data: bytes

    def encode(self) -> bytes:
        if not 0 <= self.command <= 255 or len(self.data) > 255:
            raise ValueError("Packet command or length exceeds protocol limits")
        body = bytes((self.command, len(self.data))) + self.data
        return b"\x55\x55" + body + bytes((reduce(xor, body, 0),)) + b"\xaa\xaa"


class Decoder:
    """Handle fragmented and bundled GATT notifications, validating every frame."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[Packet]:
        self.buffer.extend(data)
        packets = []
        while len(self.buffer) >= 4:
            if self.buffer[:2] != b"\x55\x55":
                raise ProtocolError("Invalid packet header")
            length = self.buffer[3] + 7
            if len(self.buffer) < length:
                break
            frame = bytes(self.buffer[:length])
            del self.buffer[:length]
            if frame[-2:] != b"\xaa\xaa" or reduce(xor, frame[2:-3], 0) != frame[-3]:
                raise ProtocolError("Invalid packet checksum or trailer")
            packets.append(Packet(frame[2], frame[4:-3]))
        return packets


def decode_png(encoded: str) -> Image.Image:
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 1_000_000:
            raise ValueError("PNG must be at most 1 MB")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG" or not (
                    1 <= image.width <= 96 and 1 <= image.height <= 2400
                ):
                    raise ValueError("D110_M PNG must be 1–96 pixels wide and 1–2400 pixels high")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Animated PNGs are not supported")
                rgba = image.convert("RGBA")
                white = Image.new("RGBA", rgba.size, "white")
                white.alpha_composite(rgba)
                return white.convert("L").point(lambda p: 255 if p >= 128 else 0, "1")
    except (
        binascii.Error,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Invalid or oversized PNG") from exc


def raster_rows(image: Image.Image):
    """Fixed 96-dot head width; MSB-first black pixels, white right padding."""
    for y in range(image.height):
        row = bytearray(12)
        for x in range(image.width):
            if image.getpixel((x, y)) == 0:
                row[x // 8] |= 1 << (7 - x % 8)
        # Zero pixel-count fields follow the NiimPrintX D110_M implementation.
        yield Packet(0x85, struct.pack(">H3BB", y, 0, 0, 0, 1) + row)


def start_payload(copies: int, profile: str) -> bytes:
    # Big endian explicitly, avoiding the fork's native-endian struct.pack('H').
    data = struct.pack(">H", copies) + b"\x00" * 5
    return data + b"\x00\x00" if profile == "d110_m_v4" else data


def dimension_payload(height: int, copies: int, profile: str) -> bytes:
    data = struct.pack(">HHH", height, 96, copies)
    return data + b"\x00" * 7 if profile == "d110_m_v4" else data


def parse_status(data: bytes) -> tuple[int, int, int]:
    if len(data) not in (4, 8, 10):
        raise ProtocolError(f"Unsupported print-status length: {len(data)}")
    if len(data) == 10 and data[6]:
        raise ProtocolError(f"Printer reported error {data[6]}")
    return struct.unpack(">HBB", data[:4])

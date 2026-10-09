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

from .models import PaperInfo, RfidInfo


class ProtocolError(RuntimeError):
    pass


class CommandUnsupported(ProtocolError):
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


def decode_png(encoded: str, head_pixels: int = 96) -> Image.Image:
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 1_000_000:
            raise ValueError("PNG must be at most 1 MB")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG" or not (
                    1 <= image.width <= head_pixels and 1 <= image.height <= 2400
                ):
                    raise ValueError(
                        f"PNG must be 1–{head_pixels} pixels wide and 1–2400 pixels high"
                    )
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


def raster_rows(image: Image.Image, head_pixels: int = 96):
    """Model-specific head width; MSB-first black pixels, white right padding."""
    for y in range(image.height):
        row = bytearray(head_pixels // 8)
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
    data = struct.pack(">HHH", height, 384 if profile == "b1" else 96, copies)
    return data + b"\x00" * 7 if profile == "d110_m_v4" else data


def parse_status(data: bytes) -> tuple[int, int, int]:
    if len(data) not in (4, 8, 10):
        raise ProtocolError(f"Unsupported print-status length: {len(data)}")
    if len(data) == 10 and data[6]:
        raise ProtocolError(f"Printer reported error {data[6]}")
    return struct.unpack(">HBB", data[:4])


def parse_rfid(data: bytes) -> RfidInfo:
    # A zero byte in a valid eight-byte UID is NOT a missing-tag indicator.
    if data == b"\x00":
        return RfidInfo(
            status="no_tag",
            tag_present=False,
            message="No RFID tag detected; this does not prove the printer is empty",
        )
    if len(data) < 15:
        raise ProtocolError("Truncated or unrecognized RFID reply")
    offset = 8

    def text_field():
        nonlocal offset
        if offset >= len(data):
            raise ProtocolError("Missing RFID string length")
        length = data[offset]
        offset += 1
        if offset + length > len(data):
            raise ProtocolError("Truncated RFID string")
        try:
            value = data[offset : offset + length].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("RFID string is not UTF-8") from exc
        offset += length
        return value

    barcode, serial = text_field(), text_field()
    if len(data) - offset < 5:
        raise ProtocolError("Truncated RFID counters")
    total, used, kind = struct.unpack_from(">HHB", data, offset)
    offset += 5
    result = RfidInfo(
        status="available",
        tag_present=True,
        tag_uid=data[:8].hex(),
        barcode=barcode,
        serial=serial,
        total=total,
        used=used,
        remaining=total - used if used <= total else None,
        consumables_type=kind,
        message="Counter units are firmware/stock dependent; not assumed to be labels",
    )
    if used > total:
        result.message = "Used counter exceeds total; remaining quantity is unknown"
    if len(data) - offset >= 2:
        result.capacity = struct.unpack_from(">H", data, offset)[0]
        offset += 2
    if len(data) - offset >= 8:
        result.secondary_uid = data[offset : offset + 8].hex()
        offset += 8
    result.extra_hex = data[offset:].hex()
    return result


def parse_paper(data: bytes) -> PaperInfo:
    if len(data) != 18:
        return PaperInfo(
            raw_hex=data.hex(), message="Unrecognized paper-info layout; dimensions unknown"
        )
    gap_px, total_px, kind, gap, total, width_px, width, _direction, _tail_px, _tail = (
        struct.unpack(">HHBHHHHBHH", data)
    )
    if total <= gap or total_px <= gap_px or not width or not width_px:
        return PaperInfo(raw_hex=data.hex(), message="Invalid paper dimensions reported")
    return PaperInfo(
        status="available",
        width_mm=width / 10,
        height_mm=(total - gap) / 10,
        gap_mm=gap / 10,
        width_pixels=width_px,
        height_pixels=total_px - gap_px,
        consumables_type=kind,
        raw_hex=data.hex(),
        message="Printer-reported dimensions; not an independent physical measurement",
    )

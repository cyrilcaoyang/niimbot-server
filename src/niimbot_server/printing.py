"""Offline label preparation; no Bluetooth imports or device I/O."""

import base64
import hashlib
import io
import math
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont
from pydantic import Field

from .models import PrintRequest, StrictModel
from .protocol import decode_png


class PreparedLabel(StrictModel):
    equipment_id: str
    profile: str
    width_mm: float = Field(gt=0, le=100)
    length_mm: float = Field(gt=0, le=300)
    request: PrintRequest

    def digest(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def validate_for(self, settings):
        if (self.equipment_id, self.profile) != (settings.equipment_id, settings.profile):
            raise ValueError("Prepared label belongs to a different printer/profile")
        if self.request.density > settings.max_density:
            raise ValueError("Density exceeds the selected printer's limit")
        with decode_png(self.request.png_base64, settings.head_pixels) as image:
            width, height = canvas_size(settings, self.width_mm, self.length_mm)
            if image.size != (width, height):
                raise ValueError("Image dimensions do not match the declared stock")


def canvas_size(settings, width_mm, length_mm):
    if not all(math.isfinite(x) and x > 0 for x in (width_mm, length_mm)):
        raise ValueError("Measure and supply positive finite stock dimensions in millimeters")
    # The medium may be wider than the print head; only the printable head is rendered.
    width = min(settings.head_pixels, math.floor(width_mm * 203 / 25.4))
    height = math.floor(length_mm * 203 / 25.4)
    if not 1 <= width or not 1 <= height <= 2400:
        raise ValueError("Stock dimensions exceed the printable canvas limits")
    return width, height


def prepare(settings, width_mm, length_mm, *, text=None, png=None, copies=1, density=3):
    if (text is None) == (png is None):
        raise ValueError("Supply exactly one of text or PNG")
    size = canvas_size(settings, width_mm, length_mm)
    if png is not None:
        image = decode_png(base64.b64encode(png).decode(), settings.head_pixels)
        if image.size != size:
            image.close()
            raise ValueError(f"PNG must be exactly {size[0]} x {size[1]} pixels; no auto-scaling")
    else:
        if not text.strip() or len(text) > 2000:
            raise ValueError("Text must contain 1–2000 characters")
        image = Image.new("1", size, 1)
        draw = ImageDraw.Draw(image)
        margin = 4
        for font_size in range(48, 7, -1):
            font = ImageFont.load_default(size=font_size)
            box = draw.multiline_textbbox((0, 0), text, font=font, spacing=2)
            if box[2] - box[0] <= size[0] - 2 * margin and box[3] - box[1] <= size[1] - 2 * margin:
                draw.multiline_text(
                    (margin - box[0], margin - box[1]), text, font=font, fill=0, spacing=2
                )
                break
        else:
            image.close()
            raise ValueError("Text does not fit legibly; add line breaks, shorten it or supply PNG")
    output = io.BytesIO()
    image.save(output, format="PNG")
    image.close()
    label = PreparedLabel(
        equipment_id=settings.equipment_id,
        profile=settings.profile,
        width_mm=width_mm,
        length_mm=length_mm,
        request=PrintRequest(
            job_id=uuid4(),
            png_base64=base64.b64encode(output.getvalue()).decode(),
            copies=copies,
            density=density,
        ),
    )
    label.validate_for(settings)
    return label

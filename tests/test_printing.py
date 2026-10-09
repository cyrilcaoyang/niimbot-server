import base64

import pytest
from test_protocol import png

from niimbot_server.models import Settings
from niimbot_server.printing import PreparedLabel, canvas_size, prepare
from niimbot_server.protocol import decode_png


def test_prepare_roundtrip_preview_and_binding():
    settings = Settings(profile="b1")
    label = prepare(settings, 40, 30, text="TEST\nLABEL")
    restored = PreparedLabel.model_validate_json(label.model_dump_json())
    assert restored.digest() == label.digest()
    with decode_png(label.request.png_base64, 384) as image:
        assert image.size == (319, 239)
        assert image.getextrema() == (0, 255)
    restored.request.copies = 2
    assert restored.digest() != label.digest()
    with pytest.raises(ValueError, match="different printer"):
        label.validate_for(Settings())


@pytest.mark.parametrize("width,length", [(0, 20), (12, -1), (float("nan"), 2), (12, 999)])
def test_invalid_stock_dimensions(width, length):
    with pytest.raises(ValueError):
        prepare(Settings(), width, length, text="test")


def test_png_must_match_stock_and_density_limits():
    settings = Settings()
    size = canvas_size(settings, 12.5, 30)
    label = prepare(settings, 12.5, 30, png=base64.b64decode(png(*size)))
    assert label.request.copies == 1
    with pytest.raises(ValueError, match="exactly"):
        prepare(settings, 12.5, 30, png=base64.b64decode(png(10, 10)))
    with pytest.raises(ValueError, match="Density"):
        prepare(settings, 12.5, 30, text="TEST", density=5)
    with pytest.raises(ValueError, match="fit"):
        prepare(settings, 12.5, 30, text="very long line " * 100)

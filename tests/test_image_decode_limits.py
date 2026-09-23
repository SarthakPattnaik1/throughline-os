from __future__ import annotations

from PIL import Image
import pytest

from throughline_domain import digitise, image_safety, images


def _compressed_large_canvas(tmp_path):
    path = tmp_path / "flat.png"
    Image.new("RGB", (1000, 1000), "white").save(path, optimize=True)
    # The whole point: compressed bytes are tiny compared with the decoded
    # allocation, so a request-byte cap cannot stand in for a pixel cap.
    assert path.stat().st_size < 100_000
    return path


def test_image_comparison_refuses_oversized_decoded_canvas(tmp_path, monkeypatch):
    path = _compressed_large_canvas(tmp_path)
    monkeypatch.setattr(image_safety, "MAX_DECODED_PIXELS", 500_000)

    with pytest.raises(images.ImageError, match="decoded pixels"):
        images.compare(
            {"path": str(path), "title": "left"},
            {"path": str(path), "title": "right"},
        )


def test_digitising_refuses_before_opencv_decodes_the_canvas(tmp_path, monkeypatch):
    path = _compressed_large_canvas(tmp_path)
    monkeypatch.setattr(image_safety, "MAX_DECODED_PIXELS", 500_000)

    calibration = digitise.Calibration(
        x1_px=0, x1_value=0, x2_px=999, x2_value=1,
        y1_px=0, y1_value=0, y2_px=999, y2_value=1,
    )

    with pytest.raises(digitise.DigitiseError, match="decoded pixels"):
        digitise.digitise(
            path=str(path),
            calibration=calibration,
            axes_declared=True,
        )


def test_normal_publication_sized_image_is_accepted(tmp_path):
    path = tmp_path / "normal.png"
    Image.new("RGB", (1200, 800), "white").save(path)

    assert image_safety.checked_dimensions(path) == (1200, 800)

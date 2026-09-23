"""Shared decoded-image safety boundary.

Compressed byte size is not decoded memory size. A PNG containing a huge flat
canvas can be tiny on disk and expand to hundreds of megabytes before OpenCV or
Pillow has a chance to resize it. Every server-side image path checks the header
through this module before asking an image library to materialise pixels.
"""

from __future__ import annotations

from pathlib import Path

# About a 6,300 x 6,300 image. Large enough for a high-resolution journal plate,
# while keeping one decoded 3-channel image around 120 MB before temporary arrays.
MAX_DECODED_PIXELS = 40_000_000


class UnsafeImage(RuntimeError):
    """An image would require an unreasonable decoded allocation."""


def checked_dimensions(path: str | Path) -> tuple[int, int]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, UnidentifiedImageError) as exc:
        raise UnsafeImage(f"That file could not be read as an image ({exc}).") from exc

    if width <= 0 or height <= 0:
        raise UnsafeImage("That image reports invalid dimensions.")

    pixels = width * height
    if pixels > MAX_DECODED_PIXELS:
        raise UnsafeImage(
            f"That image is {width}×{height} ({pixels:,} pixels). "
            f"Throughline refuses images above {MAX_DECODED_PIXELS:,} decoded "
            "pixels because compressed image size does not bound the memory "
            "needed by image analysis."
        )
    return width, height

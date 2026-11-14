"""Frame encoding and orientation helpers (core/lcd.py), no hardware needed."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from lovingcool_control.core.lcd import (
    encode_jpeg_capped,
    image_to_rgb565,
    logical_canvas_size,
    orient_for_device,
)


def noisy_image(w: int, h: int) -> Image.Image:
    # Random noise compresses badly, so the size cap actually has to kick in.
    return Image.frombytes("RGB", (w, h), bytes((i * 7919) % 251 for i in range(w * h * 3)))


def test_jpeg_fits_under_cap_and_decodes():
    data = encode_jpeg_capped(noisy_image(480, 480), max_size_kb=50)
    assert len(data) <= 50 * 1024
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG" and img.size == (480, 480)


def test_jpeg_small_image_keeps_top_quality():
    flat = Image.new("RGB", (64, 64), (10, 20, 30))
    assert encode_jpeg_capped(flat, max_size_kb=50) == encode_jpeg_capped(flat, max_size_kb=10_000)


def test_rgb565_little_endian():
    img = Image.new("RGB", (2, 1))
    img.putpixel((0, 0), (255, 0, 0))
    img.putpixel((1, 0), (0, 0, 255))
    assert image_to_rgb565(img) == bytes([0x00, 0xF8, 0x1F, 0x00])


@pytest.mark.parametrize(
    ("orientation", "expected"), [("landscape", (480, 320)), ("portrait", (320, 480))]
)
def test_logical_canvas_size(orientation, expected):
    assert logical_canvas_size(480, 320, orientation) == expected


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_orient_for_device_always_returns_native_raster(orientation):
    logical = logical_canvas_size(480, 320, orientation)
    out = orient_for_device(Image.new("RGB", logical), 480, 320, orientation)
    assert out.size == (480, 320)


def test_portrait_rotates_content_into_native_buffer():
    # A marker at the logical top-left should land at the native bottom-left
    # after the 90-degree counter-clockwise rotate.
    img = Image.new("RGB", (320, 480))
    img.putpixel((0, 0), (255, 255, 255))
    out = orient_for_device(img, 480, 320, "portrait")
    assert out.getpixel((0, 319)) == (255, 255, 255)

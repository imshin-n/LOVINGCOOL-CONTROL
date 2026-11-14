"""Frame encoding and orientation helpers (core/lcd.py), no hardware needed."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from lovingcool_control.core.lcd import (
    encode_jpeg_capped,
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


@pytest.mark.parametrize("hint", [None, 1, 20, 55, 90, 100])
def test_fit_jpeg_hint_gives_same_answer(hint):
    from lovingcool_control.core.lcd import fit_jpeg

    img = noisy_image(240, 240)
    best = fit_jpeg(img, 20)
    assert fit_jpeg(img, 20, hint=hint)[1] == best[1]
    assert len(best[0]) <= 20 * 1024


def test_fit_jpeg_returns_highest_fitting_quality():
    from lovingcool_control.core.lcd import fit_jpeg

    img = noisy_image(240, 240)
    data, q = fit_jpeg(img, 20)
    over = io.BytesIO()
    img.save(over, format="JPEG", quality=q + 1)
    assert len(over.getvalue()) > 20 * 1024


def test_non_jpeg_screens_are_rejected_clearly(make_device):
    from lovingcool_control.core.device import DeviceError
    from lovingcool_control.core.lcd import LCDController

    d, _ = make_device(lcd_color_format="RGB565")
    ctl = LCDController(d)
    ctl._info_loaded = True
    with pytest.raises(DeviceError, match="Only JPEG"):
        ctl.send_image(Image.new("RGB", (10, 10)))


def test_text_is_centred():
    from lovingcool_control.core.lcd import render_text_image

    img = render_text_image("X", 100, 100, font_size=40, fg_color=(255, 255, 255), bg_color=(0, 0, 0))
    box = img.getbbox()
    assert box and abs((box[0] + box[2]) / 2 - 50) <= 3 and abs((box[1] + box[3]) / 2 - 50) <= 3

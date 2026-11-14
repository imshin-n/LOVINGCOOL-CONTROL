"""In-app image editing: the pure edit pipeline, saving copies, and the editor dialog."""

from __future__ import annotations

import os

import pytest
from PIL import Image

from lovingcool_control.core.image_edit import (
    ImageEdits,
    apply_edits,
    crop_box,
    edits_for,
    is_editable,
    load_edits,
    save_edited,
    sidecar_for,
)


def quadrants(w=200, h=100) -> Image.Image:
    """Red top-left, green top-right, blue bottom-left, white bottom-right."""
    img = Image.new("RGB", (w, h))
    for (x, y), c in {(0, 0): (255, 0, 0), (1, 0): (0, 255, 0), (0, 1): (0, 0, 255), (1, 1): (255, 255, 255)}.items():
        img.paste(c, (x * w // 2, y * h // 2, (x + 1) * w // 2, (y + 1) * h // 2))
    return img


def test_no_edits_changes_nothing():
    img = quadrants()
    assert ImageEdits(source="x").is_identity
    assert apply_edits(img, ImageEdits(source="x")).tobytes() == img.tobytes()


def test_crop_takes_the_given_fraction():
    out = apply_edits(quadrants(), ImageEdits(source="x", crop=(0.5, 0.0, 1.0, 0.5)))
    assert out.size == (100, 50)
    assert out.getpixel((50, 25)) == (0, 255, 0)


def test_crop_box_is_clamped_and_never_empty():
    assert crop_box((100, 100), (-1, 0.5, 2, 0.5)) == (0, 50, 100, 51)
    assert crop_box((100, 100), None) == (0, 0, 100, 100)


@pytest.mark.parametrize("rotate, corner", [(90, (0, 0, 255)), (180, (255, 255, 255)), (270, (0, 255, 0))])
def test_rotation_is_clockwise(rotate, corner):
    out = apply_edits(quadrants(), ImageEdits(source="x", rotate=rotate))
    assert out.size == ((100, 200) if rotate != 180 else (200, 100))
    assert out.getpixel((2, 2)) == corner


def test_flips():
    assert apply_edits(quadrants(), ImageEdits(source="x", flip_h=True)).getpixel((2, 2)) == (0, 255, 0)
    assert apply_edits(quadrants(), ImageEdits(source="x", flip_v=True)).getpixel((2, 2)) == (0, 0, 255)


def test_crop_applies_after_turning():
    # After a right turn the old bottom-left (blue) is the new top-left
    out = apply_edits(quadrants(), ImageEdits(source="x", rotate=90, crop=(0, 0, 0.5, 0.5)))
    assert out.size == (50, 100)
    assert [c for _n, c in out.getcolors()] == [(0, 0, 255)]


def test_adjustments_keep_transparency():
    img = Image.new("RGBA", (10, 10), (100, 100, 100, 80))
    out = apply_edits(img, ImageEdits(source="x", brightness=2.0))
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0)) == (200, 200, 200, 80)


def test_large_results_are_shrunk():
    out = apply_edits(Image.new("RGB", (4000, 2000)), ImageEdits(source="x"), max_side=1000)
    assert out.size == (1000, 500)


def test_saved_copy_reopens_from_the_original(tmp_path):
    src = tmp_path / "photo.jpg"
    quadrants().save(src)
    edits = ImageEdits(source=str(src), rotate=90, crop=(0, 0, 0.5, 0.5), contrast=1.3)
    out = save_edited(edits, folder=tmp_path / "media")
    assert out.suffix == ".png" and out.is_file()
    assert src.read_bytes() == (tmp_path / "photo.jpg").read_bytes()   # original untouched
    reopened = edits_for(out)
    assert reopened.source == str(src.resolve())
    assert (reopened.rotate, reopened.crop, reopened.contrast) == (90, (0, 0, 0.5, 0.5), 1.3)
    # Saving again replaces the same copy instead of piling up new ones
    assert save_edited(reopened, folder=tmp_path / "media") == out
    assert len(list((tmp_path / "media").glob("*.png"))) == 1


def test_copy_whose_original_is_gone_edits_itself(tmp_path):
    src = tmp_path / "a.png"
    quadrants().save(src)
    out = save_edited(ImageEdits(source=str(src), flip_h=True), folder=tmp_path)
    src.unlink()
    assert load_edits(out) is None
    assert edits_for(out).source == str(out.resolve())


def test_animated_gif_edits_every_frame(tmp_path):
    src = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (80, 40), c) for c in ((255, 0, 0), (0, 0, 255), (0, 255, 0))]
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=[50, 60, 70], loop=0)
    out = save_edited(ImageEdits(source=str(src), crop=(0, 0, 0.5, 1.0)), folder=tmp_path / "m")
    assert out.suffix == ".gif" and sidecar_for(out).is_file()
    with Image.open(out) as gif:
        assert gif.n_frames == 3
        assert gif.size == (40, 40)
        gif.seek(1)
        assert gif.info["duration"] == 60
        assert gif.convert("RGB").getpixel((5, 5)) == (0, 0, 255)


def test_only_stills_and_gifs_are_editable():
    assert is_editable("/a/b.PNG") and is_editable("x.gif") and is_editable("x.webp")
    assert not is_editable("clip.mp4") and not is_editable(None)


# ── The dialog ───────────────────────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def media_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


def test_dialog_defaults_to_the_screen_shape_and_saves(qapp, media_home):
    from lovingcool_control.gui.widgets.image_editor import ImageEditorDialog

    src = media_home / "wide.png"
    quadrants(400, 200).save(src)
    d = ImageEditorDialog(str(src), (480, 480))
    left, top, right, bottom = d.canvas.crop_fractions()
    assert (right - left) * 400 == pytest.approx((bottom - top) * 200, abs=1)   # square box
    assert d.lbl_size.text() == "200 × 200 px"
    d.sliders["brightness"].setValue(20)
    d.resize(900, 600)
    assert not d.grab().isNull()   # paints without errors
    d._save()
    assert d.result_path and d.result_path.endswith("-edited.png")
    assert "lovingcool-control/media" in d.result_path
    with Image.open(d.result_path) as out:
        assert out.size == (200, 200)
    assert load_edits(d.result_path).brightness == pytest.approx(1.2)


def test_dialog_rotate_keeps_crop_shape_and_reopens_settings(qapp, media_home):
    from lovingcool_control.gui.widgets.image_editor import ImageEditorDialog

    src = media_home / "wide.png"
    quadrants(400, 200).save(src)
    d = ImageEditorDialog(str(src), (480, 480))
    d._rotate(90)
    left, top, right, bottom = d.canvas.crop_fractions()
    assert (right - left) * 200 == pytest.approx((bottom - top) * 400, abs=1)
    d.shape.setCurrentIndex(d.shape.findData("free"))
    d.canvas.set_crop_fractions((0.1, 0.2, 0.6, 0.5))
    d._save()

    again = ImageEditorDialog(d.result_path, (480, 480))
    assert again.edits.rotate == 90
    assert again.shape.currentData() == "free"
    assert again.canvas.crop_fractions() == pytest.approx((0.1, 0.2, 0.6, 0.5), abs=1e-3)

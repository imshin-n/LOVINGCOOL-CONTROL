"""
lovingcool-control :: core/image_edit.py
========================================
Simple, non-destructive picture edits made inside the app: quarter-turn
rotation, flips, a crop and brightness / contrast / saturation.

The original file is never touched. Saving writes an edited copy to
~/.config/lovingcool-control/media/ next to a small JSON file recording
the source and the settings, so opening the copy in the editor again
starts from the original rather than cropping a crop. Animated GIFs get
the same edits on every frame.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from .device import user_config_dir

STILL_MAX_SIDE = 1280   # plenty for a 480 px panel, even after the scene scales it
GIF_MAX_SIDE = 640      # GIFs keep every frame in memory, so stay closer to panel size
SIDECAR_SUFFIX = ".edit.json"

Crop = tuple[float, float, float, float]   # left, top, right, bottom as fractions of the turned image


@dataclass
class ImageEdits:
    source: str
    rotate: int = 0                 # clockwise quarter turns, in degrees: 0, 90, 180 or 270
    flip_h: bool = False
    flip_v: bool = False
    crop: Crop | None = None        # None keeps the whole picture
    brightness: float = 1.0         # 1.0 leaves it as it is
    contrast: float = 1.0
    saturation: float = 1.0

    @property
    def is_identity(self) -> bool:
        return (self.rotate % 360 == 0 and not self.flip_h and not self.flip_v and self.crop in (None, (0, 0, 1, 1))
                and self.brightness == self.contrast == self.saturation == 1.0)


def media_dir() -> Path:
    return user_config_dir() / "media"


def open_upright(path: str | Path) -> Image.Image:
    """Open an image the way it is meant to be seen (phone photos carry their rotation in EXIF)."""
    with Image.open(path) as img:
        img.load()
        return ImageOps.exif_transpose(img) or img.copy()


def turn(img: Image.Image, edits: ImageEdits) -> Image.Image:
    """Apply the rotation and flips: the picture the crop box is drawn on."""
    quarter = (edits.rotate // 90) % 4
    if quarter:
        img = img.transpose((None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180,
                             Image.Transpose.ROTATE_90)[quarter])
    if edits.flip_h:
        img = ImageOps.mirror(img)
    if edits.flip_v:
        img = ImageOps.flip(img)
    return img


def crop_box(size: tuple[int, int], crop: Crop | None) -> tuple[int, int, int, int]:
    """The crop in pixels for an image of this size, kept inside it and at least 1 px."""
    w, h = size
    if crop is None:
        return 0, 0, w, h
    left, top, right, bottom = (min(1.0, max(0.0, float(v))) for v in crop)
    x0, y0 = round(min(left, right) * w), round(min(top, bottom) * h)
    x1, y1 = round(max(left, right) * w), round(max(top, bottom) * h)
    return x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)


def adjust(img: Image.Image, edits: ImageEdits) -> Image.Image:
    """Brightness, contrast and saturation. Transparency is kept."""
    if edits.brightness == edits.contrast == edits.saturation == 1.0:
        return img
    alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") else None
    out = img.convert("RGB")
    for enhancer, amount in ((ImageEnhance.Brightness, edits.brightness),
                             (ImageEnhance.Contrast, edits.contrast),
                             (ImageEnhance.Color, edits.saturation)):
        if amount != 1.0:
            out = enhancer(out).enhance(max(0.0, amount))
    if alpha is not None:
        out.putalpha(alpha)
    return out


def apply_edits(img: Image.Image, edits: ImageEdits, max_side: int | None = None) -> Image.Image:
    """Turn, crop and adjust one picture (or one GIF frame), then shrink it to max_side if larger."""
    out = turn(img, edits)
    box = crop_box(out.size, edits.crop)
    if box != (0, 0, *out.size):
        out = out.crop(box)
    out = adjust(out, edits)
    if max_side and max(out.size) > max_side:
        out = out.copy()
        out.thumbnail((max_side, max_side), Image.LANCZOS)
    return out


# ── Saving and reopening ─────────────────────────────────────

def is_animated(path: str | Path) -> bool:
    with Image.open(path) as img:
        return getattr(img, "n_frames", 1) > 1


def output_path_for(source: str | Path, animated: bool, folder: Path | None = None) -> Path:
    """One stable copy per source file, so editing it again replaces the last copy."""
    source = Path(source).expanduser().resolve()
    tag = hashlib.sha1(str(source).encode()).hexdigest()[:8]
    return (folder or media_dir()) / f"{source.stem}-{tag}-edited{'.gif' if animated else '.png'}"


def sidecar_for(output: str | Path) -> Path:
    output = Path(output)
    return output.with_name(output.name + SIDECAR_SUFFIX)


def save_edited(edits: ImageEdits, folder: Path | None = None) -> Path:
    """Write the edited copy (PNG, or GIF if the source is animated) and its settings. Returns the copy's path."""
    source = Path(edits.source).expanduser()
    animated = is_animated(source)
    out = output_path_for(source, animated, folder)
    out.parent.mkdir(parents=True, exist_ok=True)

    if animated:
        frames, durations = [], []
        with Image.open(source) as gif:
            loop = gif.info.get("loop", 0)
            for index in range(gif.n_frames):
                gif.seek(index)
                frames.append(apply_edits(gif.convert("RGBA"), edits, GIF_MAX_SIDE))
                durations.append(int(gif.info.get("duration") or 100))
        frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations,
                       loop=loop, disposal=2, optimize=False)
    else:
        apply_edits(open_upright(source), edits, STILL_MAX_SIDE).save(out, optimize=True)

    data = dataclasses.asdict(edits)
    data["source"] = str(source.resolve())
    sidecar_for(out).write_text(json.dumps(data, indent=2), encoding="utf8")
    return out


def load_edits(path: str | Path) -> ImageEdits | None:
    """The settings an edited copy was made with, or None if this file isn't one (or its original is gone)."""
    sidecar = sidecar_for(path)
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf8"))
        crop = data.get("crop")
        edits = ImageEdits(
            source=str(data["source"]),
            rotate=int(data.get("rotate", 0)) % 360 // 90 * 90,
            flip_h=bool(data.get("flip_h", False)),
            flip_v=bool(data.get("flip_v", False)),
            crop=tuple(float(v) for v in crop) if crop and len(crop) == 4 else None,
            brightness=float(data.get("brightness", 1.0)),
            contrast=float(data.get("contrast", 1.0)),
            saturation=float(data.get("saturation", 1.0)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return edits if Path(edits.source).is_file() else None


def edits_for(path: str | Path) -> ImageEdits:
    """What the editor should open with for this file: its saved edits if it is an edited copy, else a fresh start."""
    return load_edits(path) or ImageEdits(source=str(Path(path).expanduser().resolve()))


def is_editable(path: str | None) -> bool:
    """Stills and GIFs can be edited; videos can't."""
    return bool(path) and Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

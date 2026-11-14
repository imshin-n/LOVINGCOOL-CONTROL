"""
lovingcool-control :: core/canvas.py
=============================
Screen Designer's scene composer: layers a background (solid color,
static image, GIF, or video) with live overlay widgets (hardware stat
readouts, clock, a CPU history sparkline, custom text), flattens
transparent backgrounds onto a chosen fill color, applies an arbitrary
(not just 90-degree) rotation to the whole composed frame, and streams
the result to the LCD via LCDController — all on a background thread so
the GUI never blocks on sensor polling / JPEG encoding / serial I/O.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import sensors
from .lcd import (
    Frame,
    LCDController,
    cycle_frames,
    iter_video_frames,
    load_gif_frames,
    logical_canvas_size,
    orient_for_device,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  Scene description
# ──────────────────────────────────────────────────────────────

@dataclass
class Widget:
    kind: str                              # "text" | "stat" | "cpu_graph"
    x: int = 10
    y: int = 10
    font_size: int = 28
    color: tuple[int, int, int] = (255, 255, 255)
    text: str = ""                         # kind == "text"
    metric_key: str = "cpu_percent"        # kind == "stat"
    show_label: bool = True                # kind == "stat": "CPU: 42%" vs "42%"
    width: int = 140                       # kind == "cpu_graph"
    height: int = 48                       # kind == "cpu_graph"


@dataclass
class BackgroundConfig:
    kind: str = "color"                    # "color" | "image" | "gif" | "video"
    path: str | None = None
    color: tuple[int, int, int] = (14, 17, 20)
    fit_mode: str = "cover"                # "cover" | "contain" | "stretch" | "center"


@dataclass
class SceneConfig:
    background: BackgroundConfig = field(default_factory=BackgroundConfig)
    transparent_fill: tuple[int, int, int] = (14, 17, 20)
    orientation: str = "landscape"         # "landscape" | "portrait" — swaps the logical canvas aspect
    rotation: float = 0.0                  # degrees, continuous fine-tune ON TOP of orientation
    widgets: list[Widget] = field(default_factory=list)
    fps: int = 10


# ──────────────────────────────────────────────────────────────
#  Background loading
# ──────────────────────────────────────────────────────────────

def open_background(cfg: BackgroundConfig, max_side: int | None = None) -> Iterator[Frame] | None:
    """
    An endless (frame, duration_ms) stream for the background, or None for
    a solid colour (drawn fresh each frame). Videos are decoded lazily and
    downscaled to max_side, so a long or 4K video costs no extra memory.
    """
    if cfg.kind == "color" or not cfg.path:
        return None
    if cfg.kind == "gif":
        return cycle_frames(load_gif_frames(cfg.path), loop=True)
    if cfg.kind == "video":
        return iter_video_frames(cfg.path, max_side=max_side, loop=True)
    img = Image.open(cfg.path)
    img.load()
    return itertools.repeat((img, 0))


@lru_cache(maxsize=8)
def _preview_frame_cached(kind: str, path: str, mtime: float) -> Image.Image:
    frames = open_background(BackgroundConfig(kind=kind, path=path), max_side=1024)
    assert frames is not None
    return next(frames)[0]


def background_preview_frame(cfg: BackgroundConfig) -> Image.Image | None:
    """First frame of the background, cached per file, for cheap static previews."""
    if cfg.kind == "color" or not cfg.path:
        return None
    return _preview_frame_cached(cfg.kind, cfg.path, Path(cfg.path).stat().st_mtime)


# ──────────────────────────────────────────────────────────────
#  Compositing
# ──────────────────────────────────────────────────────────────

def _flatten_transparency(img: Image.Image, fill_color: tuple[int, int, int]) -> Image.Image:
    """Composite an alpha-having image onto a solid background color."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, fill_color)
        base.paste(rgba, mask=rgba.split()[-1])
        return base
    return img.convert("RGB")


def _cover_resize(img: Image.Image, width: int, height: int) -> Image.Image:
    """Resize to fully cover width x height (may crop), preserving aspect ratio."""
    src_w, src_h = img.size
    scale = max(width / src_w, height / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def _contain_resize(img: Image.Image, width: int, height: int, fill_color: tuple[int, int, int]) -> Image.Image:
    """Resize to fit entirely within width x height, letterboxed with fill_color."""
    src_w, src_h = img.size
    scale = min(width / src_w, height / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), fill_color)
    canvas.paste(resized, ((width - new_w) // 2, (height - new_h) // 2))
    return canvas


def _center_resize(img: Image.Image, width: int, height: int, fill_color: tuple[int, int, int]) -> Image.Image:
    """No scaling — center the image as-is, cropping or letterboxing as needed."""
    canvas = Image.new("RGB", (width, height), fill_color)
    src_w, src_h = img.size
    left = (width - src_w) // 2
    top = (height - src_h) // 2
    canvas.paste(img, (left, top))
    return canvas


def resize_to_canvas(img: Image.Image, width: int, height: int,
                      mode: str, fill_color: tuple[int, int, int]) -> Image.Image:
    """Dispatch to the chosen fit mode: cover (default) / contain / stretch / center."""
    if mode == "stretch":
        return img.resize((width, height), Image.LANCZOS)
    if mode == "contain":
        return _contain_resize(img, width, height, fill_color)
    if mode == "center":
        return _center_resize(img, width, height, fill_color)
    return _cover_resize(img, width, height)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def widget_display_text(w: Widget, metrics: dict[str, sensors.Metric]) -> str:
    """The literal text a text/stat widget renders — shared by drawing and hit-testing."""
    if w.kind == "text":
        return w.text or " "
    metric = metrics.get(w.metric_key)
    if metric is None:
        return "N/A"
    return f"{metric.label}: {metric.display}" if w.show_label else metric.display


_HITTEST_SCRATCH = Image.new("RGB", (1, 1))


def widget_bbox(w: Widget, metrics: dict[str, sensors.Metric]) -> tuple[int, int, int, int]:
    """Approximate on-canvas bounding box, for click/drag hit-testing in the GUI."""
    if w.kind == "cpu_graph":
        return (w.x, w.y, w.x + w.width, w.y + w.height)
    draw = ImageDraw.Draw(_HITTEST_SCRATCH)
    text = widget_display_text(w, metrics)
    return draw.textbbox((w.x, w.y), text, font=_load_font(w.font_size))


def _draw_widgets(img: Image.Image, widgets: list[Widget],
                   metrics: dict[str, sensors.Metric], cpu_history: sensors.CpuHistory) -> None:
    draw = ImageDraw.Draw(img)
    for w in widgets:
        if w.kind == "text":
            draw.text((w.x, w.y), w.text, fill=w.color, font=_load_font(w.font_size))

        elif w.kind == "stat":
            metric = metrics.get(w.metric_key)
            if metric is None:
                continue
            text = f"{metric.label}: {metric.display}" if w.show_label else metric.display
            draw.text((w.x, w.y), text, fill=w.color, font=_load_font(w.font_size))

        elif w.kind == "cpu_graph":
            cpu_metric = metrics.get("cpu_percent")
            history = cpu_history.sample(cpu_metric.value if cpu_metric else None)
            _draw_sparkline(draw, w, history)


def _draw_sparkline(draw: ImageDraw.ImageDraw, w: Widget, history: list[float]) -> None:
    """The 'cool feature': a live CPU-usage history graph, gauge-style."""
    x0, y0, x1, y1 = w.x, w.y, w.x + w.width, w.y + w.height
    draw.rounded_rectangle((x0, y0, x1, y1), radius=6, fill=(0, 0, 0, 0),
                            outline=tuple(min(255, c + 40) for c in w.color), width=1)
    if len(history) >= 2:
        n = len(history)
        points = []
        for i, v in enumerate(history):
            px = x0 + 4 + (i / max(1, n - 1)) * (w.width - 8)
            py = y1 - 4 - (max(0.0, min(100.0, v)) / 100.0) * (w.height - 8)
            points.append((px, py))
        draw.line(points, fill=w.color, width=2, joint="curve")
    label = f"CPU {history[-1]:.0f}%" if history else "CPU --"
    draw.text((x0 + 6, y0 + 4), label, fill=w.color, font=_load_font(max(10, w.font_size // 2)))


def compose_scene(
    cfg: SceneConfig,
    logical_width: int,
    logical_height: int,
    bg_frame: Image.Image | None,
    cpu_history: sensors.CpuHistory,
    highlight_index: int | None = None,
) -> Image.Image:
    """
    Compose one frame at the LOGICAL (already-oriented) canvas size —
    background (fit + transparency flatten) + widgets + cosmetic rotation.
    Widget x/y are relative to this canvas. This is what the GUI preview
    shows and what drag-to-move hit-testing operates against, regardless
    of landscape/portrait — the caller is responsible for picking
    logical_width/logical_height via logical_canvas_size() first.
    """
    if bg_frame is not None:
        canvas = _flatten_transparency(bg_frame, cfg.transparent_fill)
        canvas = resize_to_canvas(canvas, logical_width, logical_height, cfg.background.fit_mode, cfg.transparent_fill)
    else:
        canvas = Image.new("RGB", (logical_width, logical_height), cfg.background.color)

    metrics = sensors.read_all()
    _draw_widgets(canvas, cfg.widgets, metrics, cpu_history)

    if highlight_index is not None and 0 <= highlight_index < len(cfg.widgets):
        draw = ImageDraw.Draw(canvas)
        bbox = widget_bbox(cfg.widgets[highlight_index], metrics)
        pad = 4
        draw.rectangle((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad),
                        outline=(255, 255, 255), width=1)

    if cfg.rotation % 360 != 0:
        canvas = canvas.rotate(-cfg.rotation, resample=Image.BICUBIC,
                                expand=False, fillcolor=cfg.transparent_fill)
    return canvas


def render_frame(
    cfg: SceneConfig,
    width: int,
    height: int,
    bg_frame: Image.Image | None,
    cpu_history: sensors.CpuHistory,
    highlight_index: int | None = None,
    for_device: bool = False,
) -> Image.Image:
    """
    width/height = the device's fixed native panel resolution.

    Returns the logical, already-oriented composition by default (what
    the preview/drag editor should use). Pass for_device=True to instead
    get the final frame rotated/fit to the device's native raster,
    ready to hand to LCDController.send_image().
    """
    logical_w, logical_h = logical_canvas_size(width, height, cfg.orientation)
    composed = compose_scene(cfg, logical_w, logical_h, bg_frame, cpu_history, highlight_index)
    if for_device:
        return orient_for_device(composed, width, height, cfg.orientation, cfg.transparent_fill)
    return composed


# ──────────────────────────────────────────────────────────────
#  Live playback (runs in the LCDController's single playback slot)
# ──────────────────────────────────────────────────────────────

class ScenePlayer:
    """
    Continuously renders a SceneConfig and streams it to the LCD.

    It runs as the controller's playback, so starting a GIF or video
    elsewhere stops the scene (and vice versa) instead of both writing to
    the port at once.
    """

    def __init__(self, lcd: LCDController):
        self.lcd = lcd
        self._stop: threading.Event | None = None
        self._cpu_history = sensors.CpuHistory()
        self.last_frame: Image.Image | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._stop is not None and not self._stop.is_set() and self.lcd.playing

    def start(self, cfg: SceneConfig) -> None:
        self.lcd.ensure_info()
        native_w, native_h = self.lcd.size
        background = open_background(cfg.background, max_side=max(native_w, native_h))  # may raise
        logical_w, logical_h = logical_canvas_size(native_w, native_h, cfg.orientation)
        self.last_error = None

        def loop(stop: threading.Event) -> None:
            interval = 1.0 / max(1, cfg.fps)
            due = time.monotonic()
            while not stop.is_set():
                bg = next(background)[0] if background is not None else None
                try:
                    composed = compose_scene(cfg, logical_w, logical_h, bg, self._cpu_history)
                    self.last_frame = composed   # the GUI preview shows this (same space as drag-editing)
                    self.lcd.send_frame(orient_for_device(composed, native_w, native_h,
                                                          cfg.orientation, cfg.transparent_fill))
                except Exception as e:
                    self.last_error = str(e)
                    log.error("Scene playback error: %s", e)
                    if not self.lcd.device.connected:
                        return
                due = max(due + interval, time.monotonic())
                stop.wait(due - time.monotonic())

        self._stop = self.lcd.start_playback(loop, "scene-player")

    def stop(self) -> None:
        """Stop the scene if it is still the active playback (leave a replacement alone)."""
        if self.running:
            self.lcd.stop_playback()
            try:
                self.lcd.stop()
            except Exception:
                pass
        self._stop = None

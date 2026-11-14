"""
lovingcool-control :: core/canvas.py
====================================
Scenes: a background (colour, image, GIF or video) with widgets drawn on
top (text, a system stat, a CPU history graph), optionally rotated, and
the player that streams one to the screen.

A scene is plain data (SceneConfig) and is saved as JSON; the field names
below are that file format, so keep them stable.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

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

RGB = tuple[int, int, int]
BACKGROUND_KINDS = ("color", "image", "gif", "video")
FIT_MODES = ("cover", "contain", "stretch", "center")


# ── Scene model ──────────────────────────────────────────────

@dataclass
class Widget:
    kind: str                       # "text", "stat" or "cpu_graph"
    x: int = 10
    y: int = 10
    font_size: int = 28
    color: RGB = (255, 255, 255)
    text: str = ""                  # text widgets
    metric_key: str = "cpu_percent"  # stat widgets: a key from sensors.METRIC_CHOICES
    show_label: bool = True         # stat widgets: "CPU: 42%" rather than "42%"
    width: int = 140                # graph widgets
    height: int = 48


@dataclass
class BackgroundConfig:
    kind: str = "color"             # one of BACKGROUND_KINDS
    path: str | None = None
    color: RGB = (14, 17, 20)
    fit_mode: str = "cover"         # one of FIT_MODES


@dataclass
class SceneConfig:
    background: BackgroundConfig = field(default_factory=BackgroundConfig)
    transparent_fill: RGB = (14, 17, 20)   # behind transparent pixels, letterboxing and rotation corners
    orientation: str = "landscape"         # "portrait" composes on a swapped-aspect canvas
    rotation: float = 0.0                  # extra rotation in degrees, applied after orientation
    widgets: list[Widget] = field(default_factory=list)
    fps: int = 10


def scene_metric_keys(cfg: SceneConfig) -> set[str]:
    """The stats a scene actually shows, so only those get read."""
    keys = {w.metric_key for w in cfg.widgets if w.kind == "stat"}
    if any(w.kind == "cpu_graph" for w in cfg.widgets):
        keys.add("cpu_percent")
    return keys


# ── Backgrounds ──────────────────────────────────────────────

def open_background(cfg: BackgroundConfig, max_side: int | None = None) -> Iterator[Frame] | None:
    """
    Endless (image, duration_ms) frames for the background, or None for a
    plain colour. Videos are decoded as they play and scaled down to
    max_side, so their length and resolution don't affect memory.
    """
    if cfg.kind == "color" or not cfg.path:
        return None
    if cfg.kind == "video":
        return iter_video_frames(cfg.path, max_side=max_side, loop=True)
    if cfg.kind == "gif":
        return cycle_frames(load_gif_frames(cfg.path), loop=True)
    with Image.open(cfg.path) as still:
        still.load()
        frame = still.copy()
    return itertools.repeat((frame, 0))


@lru_cache(maxsize=8)
def _first_frame(kind: str, path: str, mtime: float) -> Image.Image:
    stream = open_background(BackgroundConfig(kind=kind, path=path), max_side=1024)
    return next(stream)[0]


def background_preview_frame(cfg: BackgroundConfig) -> Image.Image | None:
    """The background's first frame, cached per file (and refreshed if the file changes)."""
    if cfg.kind == "color" or not cfg.path:
        return None
    return _first_frame(cfg.kind, cfg.path, Path(cfg.path).stat().st_mtime)


def _opaque(img: Image.Image, fill: RGB) -> Image.Image:
    """Drop any transparency by laying the image over a solid fill."""
    if img.mode == "P" and "transparency" in img.info:
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA", "PA"):
        rgba = img.convert("RGBA")
        backdrop = Image.new("RGBA", rgba.size, fill + (255,))
        return Image.alpha_composite(backdrop, rgba).convert("RGB")
    return img.convert("RGB")


def resize_to_canvas(img: Image.Image, width: int, height: int, mode: str, fill_color: RGB) -> Image.Image:
    """
    Fit an image to width x height:
      cover   - scale to fill, cropping the overflow (centred)
      contain - scale to fit inside, padding with fill_color
      stretch - scale to exactly that size, ignoring aspect ratio
      center  - no scaling; crop or pad around the centre
    """
    size = (width, height)
    if mode == "stretch":
        return img.resize(size, Image.LANCZOS)
    if mode == "contain":
        return ImageOps.pad(img, size, method=Image.LANCZOS, color=fill_color)
    if mode == "center":
        board = Image.new("RGB", size, fill_color)
        board.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
        return board
    return ImageOps.fit(img, size, method=Image.LANCZOS)


# ── Widgets ──────────────────────────────────────────────────

@lru_cache(maxsize=64)
def _font(size: int) -> ImageFont.ImageFont:
    """The bundled default font at a size (cached: building it is the slow part of drawing text)."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:   # Pillow < 10.1 has a single fixed size
        return ImageFont.load_default()


def widget_display_text(w: Widget, metrics: dict[str, sensors.Metric]) -> str:
    """What a text or stat widget shows."""
    if w.kind == "text":
        return w.text or " "
    m = metrics.get(w.metric_key)
    if m is None:
        return "N/A"
    return f"{m.label}: {m.display}" if w.show_label else m.display


def widget_bbox(w: Widget, metrics: dict[str, sensors.Metric]) -> tuple[int, int, int, int]:
    """Where a widget sits on the canvas (left, top, right, bottom), for selection and dragging."""
    if w.kind == "cpu_graph":
        return w.x, w.y, w.x + w.width, w.y + w.height
    left, top, right, bottom = _font(w.font_size).getbbox(widget_display_text(w, metrics))
    return w.x + left, w.y + top, w.x + right, w.y + bottom


def _paint_text(draw: ImageDraw.ImageDraw, w: Widget, metrics, history) -> None:
    draw.text((w.x, w.y), widget_display_text(w, metrics), fill=tuple(w.color), font=_font(w.font_size))


def _paint_graph(draw: ImageDraw.ImageDraw, w: Widget, metrics, history: sensors.CpuHistory) -> None:
    cpu = metrics.get("cpu_percent")
    values = history.sample(cpu.value if cpu else None)
    left, top, right, bottom = w.x, w.y, w.x + w.width, w.y + w.height
    frame_color = tuple(min(255, int(c * 0.6) + 60) for c in w.color)
    draw.rounded_rectangle((left, top, right, bottom), radius=6, outline=frame_color, width=1)

    pad = 4
    inner_w, inner_h = max(1, w.width - 2 * pad), max(1, w.height - 2 * pad)
    if len(values) > 1:
        step = inner_w / (len(values) - 1)
        points = [(left + pad + i * step, bottom - pad - inner_h * min(max(v, 0.0), 100.0) / 100.0)
                  for i, v in enumerate(values)]
        draw.line(points, fill=tuple(w.color), width=2, joint="curve")

    caption = f"CPU {values[-1]:.0f}%" if values else "CPU --"
    draw.text((left + pad + 2, top + pad), caption, fill=tuple(w.color), font=_font(max(10, w.font_size // 2)))


_PAINTERS: dict[str, Callable] = {"text": _paint_text, "stat": _paint_text, "cpu_graph": _paint_graph}


# ── Rendering ────────────────────────────────────────────────

def compose_scene(
    cfg: SceneConfig,
    logical_width: int,
    logical_height: int,
    bg_frame: Image.Image | None,
    cpu_history: sensors.CpuHistory,
    highlight_index: int | None = None,
    metrics: dict[str, sensors.Metric] | None = None,
) -> Image.Image:
    """
    Draw one frame at the logical (orientation-adjusted) canvas size: what
    editors preview and what widget positions refer to. highlight_index
    outlines one widget (the designer's selection).
    """
    size = (logical_width, logical_height)
    if bg_frame is None:
        frame = Image.new("RGB", size, tuple(cfg.background.color))
    else:
        frame = resize_to_canvas(_opaque(bg_frame, tuple(cfg.transparent_fill)), *size,
                                 cfg.background.fit_mode, tuple(cfg.transparent_fill))

    if metrics is None:
        metrics = sensors.read_all(scene_metric_keys(cfg))
    draw = ImageDraw.Draw(frame)
    for w in cfg.widgets:
        painter = _PAINTERS.get(w.kind)
        if painter is not None:
            painter(draw, w, metrics, cpu_history)

    if highlight_index is not None and 0 <= highlight_index < len(cfg.widgets):
        left, top, right, bottom = widget_bbox(cfg.widgets[highlight_index], metrics)
        draw.rectangle((left - 4, top - 4, right + 4, bottom + 4), outline=(255, 255, 255), width=1)

    if cfg.rotation % 360:
        frame = frame.rotate(-cfg.rotation, resample=Image.BICUBIC, fillcolor=tuple(cfg.transparent_fill))
    return frame


def render_frame(
    cfg: SceneConfig,
    width: int,
    height: int,
    bg_frame: Image.Image | None,
    cpu_history: sensors.CpuHistory,
    highlight_index: int | None = None,
    for_device: bool = False,
    metrics: dict[str, sensors.Metric] | None = None,
) -> Image.Image:
    """
    Render for a panel of width x height. By default returns the logical
    frame (for previews); for_device=True returns it oriented to the
    panel's native raster, ready to send.
    """
    frame = compose_scene(cfg, *logical_canvas_size(width, height, cfg.orientation), bg_frame,
                          cpu_history, highlight_index, metrics)
    return orient_for_device(frame, width, height, cfg.orientation, tuple(cfg.transparent_fill)) \
        if for_device else frame


# ── Playback ─────────────────────────────────────────────────

class ScenePlayer:
    """
    Renders a scene continuously and streams it to the screen, as the
    LCDController's one playback: showing anything else stops the scene.
    """

    def __init__(self, lcd: LCDController):
        self.lcd = lcd
        self._stop: threading.Event | None = None
        self._history = sensors.CpuHistory()
        self.last_frame: Image.Image | None = None
        self.last_error: str | None = None
        self.frames_sent = 0
        self.frames_skipped = 0

    @property
    def running(self) -> bool:
        return self._stop is not None and not self._stop.is_set() and self.lcd.playing

    def start(self, cfg: SceneConfig) -> None:
        self.lcd.ensure_info()
        panel_w, panel_h = self.lcd.size
        backgrounds = open_background(cfg.background, max_side=max(panel_w, panel_h))   # may raise
        canvas_size = logical_canvas_size(panel_w, panel_h, cfg.orientation)
        period = 1.0 / max(1, cfg.fps)
        self.last_error = None

        def run(stop: threading.Event) -> None:
            previous: bytes | None = None
            next_at = time.monotonic()
            while not stop.is_set():
                background = next(backgrounds)[0] if backgrounds is not None else None
                try:
                    frame = compose_scene(cfg, *canvas_size, background, self._history)
                    pixels = frame.tobytes()
                    if pixels == previous:
                        # Unchanged (stats only refresh once a second): skip the rotate,
                        # encode and transfer. The keep-alive holds the picture meanwhile.
                        self.frames_skipped += 1
                    else:
                        self.last_frame = frame
                        self.lcd.send_frame(orient_for_device(frame, panel_w, panel_h, cfg.orientation,
                                                              tuple(cfg.transparent_fill)))
                        previous = pixels
                        self.frames_sent += 1
                except Exception as e:
                    self.last_error = str(e)
                    log.error("Scene frame failed: %s", e)
                    if not self.lcd.device.connected:
                        return
                next_at = max(next_at + period, time.monotonic())
                stop.wait(next_at - time.monotonic())

        self._stop = self.lcd.start_playback(run, "scene-player")

    def stop(self) -> None:
        """Stop this scene, unless something else has already replaced it."""
        if self.running:
            self.lcd.stop_playback()
            try:
                self.lcd.stop()
            except Exception:
                pass
        self._stop = None


# ── Scene files ──────────────────────────────────────────────

def _color(value, fallback: RGB) -> RGB:
    try:
        r, g, b = (max(0, min(255, int(c))) for c in value)
    except (TypeError, ValueError):
        return fallback
    return r, g, b


def scene_to_dict(cfg: SceneConfig) -> dict:
    data = dataclasses.asdict(cfg)
    data["transparent_fill"] = list(cfg.transparent_fill)
    return {k: data[k] for k in ("background", "transparent_fill", "orientation", "rotation", "fps", "widgets")}


def scene_from_dict(data: dict) -> SceneConfig:
    """Read scene JSON, filling gaps with defaults. Unknown widget fields raise TypeError."""
    base = SceneConfig()
    bg = data.get("background") or {}
    widgets = []
    for raw in data.get("widgets", []):
        raw = dict(raw)
        if "color" in raw:
            raw["color"] = _color(raw["color"], (255, 255, 255))
        widgets.append(Widget(**raw))
    return SceneConfig(
        background=BackgroundConfig(
            kind=bg.get("kind") if bg.get("kind") in BACKGROUND_KINDS else "color",
            path=bg.get("path"),
            color=_color(bg.get("color"), base.background.color),
            fit_mode=bg.get("fit_mode") if bg.get("fit_mode") in FIT_MODES else "cover",
        ),
        transparent_fill=_color(data.get("transparent_fill"), base.transparent_fill),
        orientation="portrait" if data.get("orientation") == "portrait" else "landscape",
        rotation=float(data.get("rotation", 0.0)),
        widgets=widgets,
        fps=min(60, max(1, int(data.get("fps", base.fps)))),
    )


def load_scene(path: str | Path) -> SceneConfig:
    return scene_from_dict(json.loads(Path(path).read_text(encoding="utf8")))


def save_scene(cfg: SceneConfig, path: str | Path) -> None:
    Path(path).write_text(json.dumps(scene_to_dict(cfg), indent=2), encoding="utf8")

"""
lovingcool-control :: gui/pages/device_view.py
==============================================
The per-device page, laid out like an RGB-controller device view: a
picture of the screen on the left, and on the right a Mode list with that
mode's options, orientation, rotation, brightness and an Apply button.
Everything goes to the service through the bridge.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core import sensors
from lovingcool_control.core.canvas import background_preview_frame, load_scene, render_frame
from lovingcool_control.core.device import user_config_dir
from lovingcool_control.core.lcd import (
    VIDEO_EXTENSIONS,
    load_image,
    load_video_thumbnail,
    logical_canvas_size,
    render_text_image,
)
from lovingcool_control.gui.bridge import ServiceBridge
from lovingcool_control.gui.widgets.preview import ScreenPreview

log = logging.getLogger(__name__)

MODES = [("media", "Image / GIF / Video"), ("text", "Text"), ("scene", "Scene"), ("off", "Off")]
MEDIA_FILTER = "Media (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.mp4 *.avi *.mov *.mkv *.webm)"


def scenes_dir() -> Path:
    return user_config_dir() / "scenes"


def saved_scenes() -> list[Path]:
    """Scene files the user saved (the designer's own working files are left out)."""
    d = scenes_dir()
    if not d.is_dir():
        return []
    hidden = {"designer.json", "designer-live.json"}
    return sorted((p for p in d.glob("*.json") if p.name not in hidden), key=lambda p: p.name.lower())


class ColorButton(QPushButton):
    """A button showing a colour swatch; click to pick a new one."""

    changed = pyqtSignal(QColor)

    def __init__(self, color: QColor, title: str):
        super().__init__()
        self._title = title
        self.setFixedWidth(90)
        self.clicked.connect(self._pick)
        self.set_color(color)

    def set_color(self, color: QColor) -> None:
        self.color = QColor(color)
        text = "#000" if self.color.lightness() > 140 else "#fff"
        self.setStyleSheet(f"background-color: {self.color.name()}; color: {text};")
        self.setText(self.color.name())

    def _pick(self) -> None:
        c = QColorDialog.getColor(self.color, self, self._title)
        if c.isValid():
            self.set_color(c)
            self.changed.emit(c)

    def rgb(self) -> list[int]:
        return [self.color.red(), self.color.green(), self.color.blue()]


class DeviceView(QWidget):
    log_message = pyqtSignal(str)
    edit_scene_requested = pyqtSignal()

    def __init__(self, bridge: ServiceBridge):
        super().__init__()
        self.bridge = bridge
        self._settings = QSettings("lovingcool-control", "lovingcool-control")
        self._file: str | None = None
        self._brightness_dirty = False
        self._build_ui()
        self._restore_settings()
        self.refresh_scenes()
        bridge.status_changed.connect(self._on_status)
        self._brightness_timer = QTimer(self, singleShot=True, interval=250)
        self._brightness_timer.timeout.connect(self._send_brightness)
        self._update_preview()

    # ── Layout ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left = QVBoxLayout()
        self.preview = ScreenPreview(300)
        left.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.lbl_size = QLabel()
        self.lbl_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.lbl_size)
        self.lbl_now = QLabel()
        self.lbl_now.setWordWrap(True)
        self.lbl_now.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_now.setMaximumWidth(300)
        left.addWidget(self.lbl_now)
        left.addStretch()
        root.addLayout(left)

        right = QVBoxLayout()
        box = QGroupBox("Mode")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.mode = QComboBox()
        for key, label in MODES:
            self.mode.addItem(label, key)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Mode:", self.mode)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._media_page())
        self.stack.addWidget(self._text_page())
        self.stack.addWidget(self._scene_page())
        off = QLabel("Turns the screen off. It stays off until something else is shown.")
        off.setWordWrap(True)
        self.stack.addWidget(off)
        form.addRow(self.stack)

        self.orientation = QComboBox()
        self.orientation.addItem("Landscape", "landscape")
        self.orientation.addItem("Portrait", "portrait")
        self.orientation.currentIndexChanged.connect(self._update_preview)
        form.addRow("Orientation:", self.orientation)

        self.rotation = QComboBox()
        for deg in (0, 90, 180, 270):
            self.rotation.addItem(f"{deg}°", deg)
        self.rotation.currentIndexChanged.connect(self._update_preview)
        form.addRow("Rotation:", self.rotation)
        right.addWidget(box)

        bright_box = QGroupBox("Brightness")
        bl = QHBoxLayout(bright_box)
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.valueChanged.connect(self._on_brightness_moved)
        self.lbl_brightness = QLabel("—")
        self.lbl_brightness.setMinimumWidth(40)
        bl.addWidget(self.brightness, stretch=1)
        bl.addWidget(self.lbl_brightness)
        right.addWidget(bright_box)

        right.addStretch()
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setDefault(True)
        self.apply_btn.setMinimumWidth(120)
        self.apply_btn.clicked.connect(self.apply)
        apply_row.addWidget(self.apply_btn)
        right.addLayout(apply_row)
        root.addLayout(right, stretch=1)

    def _media_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.file_field = QLineEdit()
        self.file_field.setReadOnly(True)
        self.file_field.setPlaceholderText("No file selected")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.file_field, stretch=1)
        row.addWidget(browse)
        f.addRow("File:", row)
        self.loop = QComboBox()
        self.loop.addItems(["Loop", "Play once"])
        f.addRow("GIF / video:", self.loop)
        return w

    def _text_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 0, 0, 0)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Text to show")
        self.text.textChanged.connect(self._update_preview)
        f.addRow("Text:", self.text)
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 300)
        self.font_size.setValue(40)
        self.font_size.setSuffix(" px")
        self.font_size.valueChanged.connect(self._update_preview)
        f.addRow("Size:", self.font_size)
        colors = QHBoxLayout()
        self.fg = ColorButton(QColor("#ffffff"), "Text colour")
        self.bg = ColorButton(QColor("#000000"), "Background colour")
        for b in (self.fg, self.bg):
            b.changed.connect(self._update_preview)
        colors.addWidget(QLabel("Text"))
        colors.addWidget(self.fg)
        colors.addSpacing(12)
        colors.addWidget(QLabel("Background"))
        colors.addWidget(self.bg)
        colors.addStretch()
        f.addRow("Colours:", colors)
        return w

    def _scene_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.scene = QComboBox()
        self.scene.currentIndexChanged.connect(self._update_preview)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_scene)
        row.addWidget(self.scene, stretch=1)
        row.addWidget(browse)
        f.addRow("Scene:", row)
        edit = QPushButton("Open Screen Designer")
        edit.clicked.connect(self.edit_scene_requested.emit)
        f.addRow("", edit)
        return w

    # ── State ─────────────────────────────────────────────────

    def current_mode(self) -> str:
        return self.mode.currentData()

    def _on_mode_changed(self) -> None:
        self.stack.setCurrentIndex(self.mode.currentIndex())
        is_off = self.current_mode() == "off"
        self.orientation.setEnabled(not is_off and self.current_mode() != "scene")
        self.rotation.setEnabled(self.current_mode() == "media")
        self._update_preview()

    def _browse(self) -> None:
        start = str(Path(self._file).parent) if self._file else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image, GIF or video", start, MEDIA_FILTER)
        if path:
            self.set_file(path)

    def set_file(self, path: str) -> None:
        self._file = path
        self.file_field.setText(Path(path).name)
        self.file_field.setToolTip(path)
        self._update_preview()

    def refresh_scenes(self, select: str | None = None) -> None:
        current = select or self.scene.currentData()
        self.scene.blockSignals(True)
        self.scene.clear()
        for p in saved_scenes():
            self.scene.addItem(p.stem, str(p))
        if current and self.scene.findData(current) < 0 and Path(current).is_file():
            self.scene.addItem(Path(current).stem, current)
        idx = self.scene.findData(current) if current else -1
        self.scene.setCurrentIndex(idx if idx >= 0 else 0)
        self.scene.blockSignals(False)
        self._update_preview()

    def _browse_scene(self) -> None:
        start = str(scenes_dir()) if scenes_dir().is_dir() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose a scene", start, "Scene (*.json)")
        if path:
            self.refresh_scenes(select=path)

    def _panel_size(self) -> tuple[int, int]:
        return self.bridge.panel_size()

    def _update_preview(self) -> None:
        w, h = self._panel_size()
        mode = self.current_mode()
        orientation = self.orientation.currentData()
        lw, lh = logical_canvas_size(w, h, orientation) if mode in ("media", "text") else (w, h)
        self.lbl_size.setText(f"{lw} × {lh} px" + ("" if self.bridge.connected else "  (last known size)"))
        try:
            if mode == "media":
                if not self._file:
                    self.preview.clear_image("Choose a file")
                    return
                img = load_video_thumbnail(self._file) if self._file.lower().endswith(VIDEO_EXTENSIONS) \
                    else load_image(self._file)
                if self.rotation.currentData():
                    img = img.convert("RGB").resize((lw, lh)).rotate(-self.rotation.currentData())
                self.preview.set_image(img)
            elif mode == "text":
                if not self.text.text():
                    self.preview.clear_image("Type some text")
                    return
                self.preview.set_image(render_text_image(self.text.text(), lw, lh, self.font_size.value(),
                                                         tuple(self.fg.rgb()), tuple(self.bg.rgb())))
            elif mode == "scene":
                path = self.scene.currentData()
                if not path:
                    self.preview.clear_image("No saved scenes yet")
                    return
                cfg = load_scene(path)
                frame = render_frame(cfg, w, h, background_preview_frame(cfg.background),
                                     sensors.CpuHistory())
                self.preview.set_image(frame)
            else:
                self.preview.set_image(Image.new("RGB", (lw, lh)))
        except Exception as e:
            self.preview.clear_image("Can't preview this")
            log.debug("Preview failed: %s", e)

    def _on_status(self, st: dict) -> None:
        self._update_size_label_only()
        content = st.get("content") or "nothing yet"
        self.lbl_now.setText(f"Now showing: {content}" + ("  ▶" if st.get("playing") else ""))
        b = st.get("brightness")
        if b is not None and not self._brightness_dirty and not self.brightness.isSliderDown():
            self.brightness.blockSignals(True)
            self.brightness.setValue(int(b))
            self.brightness.blockSignals(False)
            self.lbl_brightness.setText(f"{int(b)}%")

    def _update_size_label_only(self) -> None:
        w, h = self._panel_size()
        if self.current_mode() in ("media", "text"):
            w, h = logical_canvas_size(w, h, self.orientation.currentData())
        self.lbl_size.setText(f"{w} × {h} px" + ("" if self.bridge.connected else "  (last known size)"))

    # ── Actions ───────────────────────────────────────────────

    def build_request(self) -> tuple[dict, str] | None:
        mode = self.current_mode()
        orientation = self.orientation.currentData()
        if mode == "media":
            if not self._file:
                self.log_message.emit("[ERROR] Choose an image, GIF or video first.")
                return None
            return ({"cmd": "show", "path": self._file, "loop": self.loop.currentIndex() == 0,
                     "rotation": self.rotation.currentData(), "orientation": orientation},
                    Path(self._file).name)
        if mode == "text":
            if not self.text.text():
                self.log_message.emit("[ERROR] Type some text first.")
                return None
            return ({"cmd": "text", "text": self.text.text(), "font_size": self.font_size.value(),
                     "fg": self.fg.rgb(), "bg": self.bg.rgb(), "orientation": orientation},
                    f'"{self.text.text()}"')
        if mode == "scene":
            path = self.scene.currentData()
            if not path:
                self.log_message.emit("[ERROR] No scene chosen. Save one from the Screen Designer first.")
                return None
            return {"cmd": "scene", "path": path}, f"scene {Path(path).stem}"
        return {"cmd": "off"}, "off"

    def apply(self) -> None:
        built = self.build_request()
        if built is None:
            return
        req, label = built
        self._save_settings()
        self.apply_btn.setEnabled(False)

        def done(reply: dict) -> None:
            self.apply_btn.setEnabled(True)
            if reply.get("ok"):
                self.log_message.emit(f"[OK] Showing {label}" if reply.get("applied")
                                      else f"[INFO] Saved {label}; it will show when the cooler connects")
                self.bridge.refresh()

        self.bridge.call(req, done)

    def _on_brightness_moved(self, value: int) -> None:
        self.lbl_brightness.setText(f"{value}%")
        self._brightness_dirty = True
        self._brightness_timer.start()

    def _send_brightness(self) -> None:
        value = self.brightness.value()

        def done(_reply: dict) -> None:
            self._brightness_dirty = False

        self.bridge.call({"cmd": "brightness", "value": value}, done)

    # ── Settings ──────────────────────────────────────────────

    def _restore_settings(self) -> None:
        st = self._settings
        st.beginGroup("device_view")
        try:
            path = st.value("file", "")
            if path and Path(path).is_file():
                self.set_file(path)
            self.loop.setCurrentIndex(int(st.value("loop_index", 0)))
            self.text.setText(st.value("text", ""))
            self.font_size.setValue(int(st.value("font_size", 40)))
            self.fg.set_color(QColor(st.value("fg", "#ffffff")))
            self.bg.set_color(QColor(st.value("bg", "#000000")))
            self.orientation.setCurrentIndex(max(0, self.orientation.findData(st.value("orientation", "landscape"))))
            self.rotation.setCurrentIndex(max(0, self.rotation.findData(int(st.value("rotation", 0)))))
            self.mode.setCurrentIndex(max(0, self.mode.findData(st.value("mode", "media"))))
            scene = st.value("scene", "")
            if scene:
                self.refresh_scenes(select=scene)
        except (TypeError, ValueError):
            pass
        finally:
            st.endGroup()
        self._on_mode_changed()

    def _save_settings(self) -> None:
        st = self._settings
        st.beginGroup("device_view")
        st.setValue("file", self._file or "")
        st.setValue("loop_index", self.loop.currentIndex())
        st.setValue("text", self.text.text())
        st.setValue("font_size", self.font_size.value())
        st.setValue("fg", self.fg.color.name())
        st.setValue("bg", self.bg.color.name())
        st.setValue("orientation", self.orientation.currentData())
        st.setValue("rotation", self.rotation.currentData())
        st.setValue("mode", self.current_mode())
        st.setValue("scene", self.scene.currentData() or "")
        st.endGroup()

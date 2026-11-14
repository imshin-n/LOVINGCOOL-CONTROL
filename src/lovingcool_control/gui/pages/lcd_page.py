"""
lovingcool-control :: gui/pages/lcd_page.py
====================================
Simple LCD page: show an image, GIF, video or a line of text. Everything
goes through the service (gui/bridge.py), which keeps showing it after this
window closes when the background service is installed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core.lcd import (
    VIDEO_EXTENSIONS,
    load_image,
    load_video_thumbnail,
    logical_canvas_size,
    render_text_image,
)
from lovingcool_control.gui.bridge import ServiceBridge

log = logging.getLogger(__name__)


class LCDPreview(QLabel):
    """
    Aspect-correct preview scaled to fit within a max box — sized from the
    connected device's actual resolution (auto-detected), not a fixed guess.
    """

    def __init__(self, max_width: int = 300, max_height: int = 300):
        super().__init__()
        self._max_w = max_width
        self._max_h = max_height
        self.setFixedSize(max_width, max_height)
        self.setStyleSheet(
            "background-color: #0E1014; border: 2px solid #2C323D; border-radius: 12px;"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("No preview")
        self.setStyleSheet(self.styleSheet() + "color: #8B93A1; font-size: 12px;")

    def set_image(self, img):
        """Set preview from a PIL Image, scaled to fit while preserving aspect ratio."""
        img = img.convert("RGB")
        w, h = img.size
        scale = min(self._max_w / w, self._max_h / h)
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size)
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        self.setPixmap(pixmap)


class LCDPage(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, bridge: ServiceBridge):
        super().__init__()
        self.bridge = bridge
        self._settings = QSettings("lovingcool-control", "lovingcool-control")
        self._current_image_path: str | None = None
        self._orientation = "landscape"
        self._build_ui()
        self._restore_settings()
        self._update_dimensions_label()
        bridge.status_changed.connect(lambda _st: self._update_dimensions_label())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        # Header
        title = QLabel("LCD Display")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Send images, GIFs, videos, or text to your AIO's screen")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # Main content: preview + controls side by side
        content = QHBoxLayout()
        content.setSpacing(24)

        # Left: preview
        preview_panel = QFrame()
        preview_panel.setObjectName("panel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(24, 24, 24, 24)
        preview_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.preview = LCDPreview(300, 300)
        preview_layout.addWidget(self.preview)

        self.dimensions_label = QLabel("— × — px")
        self.dimensions_label.setObjectName("pageSubtitle")
        self.dimensions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.dimensions_label)

        content.addWidget(preview_panel, stretch=0)

        # Right: controls
        controls_panel = QFrame()
        controls_panel.setObjectName("panel")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(24, 24, 24, 24)
        controls_layout.setSpacing(16)

        # Source selector tabs (simple button row)
        source_label = QLabel("SOURCE")
        source_label.setObjectName("sectionLabel")
        controls_layout.addWidget(source_label)

        source_row = QHBoxLayout()
        self.btn_image = QPushButton("Image / GIF / Video")
        self.btn_text = QPushButton("Text")
        self.btn_image.setObjectName("primaryButton")
        for b in (self.btn_image, self.btn_text):
            b.setCheckable(True)
        self.btn_image.setChecked(True)
        self.btn_image.clicked.connect(lambda: self._switch_source("image"))
        self.btn_text.clicked.connect(lambda: self._switch_source("text"))
        source_row.addWidget(self.btn_image)
        source_row.addWidget(self.btn_text)
        controls_layout.addLayout(source_row)

        # Image / GIF / video picker
        self.image_picker_row = QHBoxLayout()
        self.file_path_field = QLineEdit()
        self.file_path_field.setReadOnly(True)
        self.file_path_field.setPlaceholderText("No file selected...")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_file)
        self.image_picker_row.addWidget(self.file_path_field)
        self.image_picker_row.addWidget(browse_btn)
        controls_layout.addLayout(self.image_picker_row)

        # GIF/video loop options
        self.gif_options_row = QHBoxLayout()
        self.loop_combo = QComboBox()
        self.loop_combo.addItems(["Loop forever", "Play once"])
        self.gif_options_row.addWidget(QLabel("GIF/video playback:"))
        self.gif_options_row.addWidget(self.loop_combo)
        self.gif_options_row.addStretch()
        controls_layout.addLayout(self.gif_options_row)

        # Text input (hidden until "Text" source selected)
        self.text_field = QLineEdit()
        self.text_field.setPlaceholderText("Enter text to display (e.g. CPU 45°C)")
        self.text_field.setVisible(False)
        self.text_field.textChanged.connect(self._update_text_preview)
        controls_layout.addWidget(self.text_field)

        text_color_row = QHBoxLayout()
        self.fg_color_btn = QPushButton("Text Color")
        self.bg_color_btn = QPushButton("Background Color")
        self._fg_color = QColor(255, 255, 255)
        self._bg_color = QColor(0, 0, 0)
        self.fg_color_btn.clicked.connect(self._pick_fg_color)
        self.bg_color_btn.clicked.connect(self._pick_bg_color)
        self.fg_color_btn.setVisible(False)
        self.bg_color_btn.setVisible(False)
        text_color_row.addWidget(self.fg_color_btn)
        text_color_row.addWidget(self.bg_color_btn)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 300)
        self.font_size_spin.setValue(40)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setVisible(False)
        self.font_size_spin.valueChanged.connect(self._update_text_preview)
        text_color_row.addWidget(self.font_size_spin)
        controls_layout.addLayout(text_color_row)

        # Orientation
        orient_label = QLabel("ORIENTATION")
        orient_label.setObjectName("sectionLabel")
        controls_layout.addWidget(orient_label)
        orient_row = QHBoxLayout()
        self.btn_landscape = QPushButton("⬛ Landscape")
        self.btn_portrait = QPushButton("▮ Portrait")
        for b in (self.btn_landscape, self.btn_portrait):
            b.setCheckable(True)
        self.btn_landscape.setChecked(True)
        self.btn_landscape.setObjectName("primaryButton")
        self.btn_landscape.clicked.connect(lambda: self._set_orientation("landscape"))
        self.btn_portrait.clicked.connect(lambda: self._set_orientation("portrait"))
        orient_row.addWidget(self.btn_landscape)
        orient_row.addWidget(self.btn_portrait)
        controls_layout.addLayout(orient_row)

        # Rotation
        rotation_label = QLabel("ROTATION (fine-tune, on top of orientation)")
        rotation_label.setObjectName("sectionLabel")
        controls_layout.addWidget(rotation_label)
        rotation_row = QHBoxLayout()
        self.rotation_combo = QComboBox()
        self.rotation_combo.addItems(["0°", "90°", "180°", "270°"])
        self.rotation_combo.currentIndexChanged.connect(self._on_rotation_or_orientation_changed)
        rotation_row.addWidget(self.rotation_combo)
        rotation_row.addStretch()
        controls_layout.addLayout(rotation_row)

        controls_layout.addStretch()

        # Action buttons
        action_row = QHBoxLayout()
        self.send_btn = QPushButton("Send to LCD")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.clicked.connect(self._send_to_device)
        self.stop_btn = QPushButton("Stop / Clear")
        self.stop_btn.clicked.connect(self._stop_display)
        action_row.addWidget(self.send_btn)
        action_row.addWidget(self.stop_btn)
        controls_layout.addLayout(action_row)

        content.addWidget(controls_panel, stretch=1)
        layout.addLayout(content)

    # ── UI state handlers ────────────────────────────────────

    def _switch_source(self, mode: str):
        is_image = mode == "image"
        self.btn_image.setChecked(is_image)
        self.btn_text.setChecked(not is_image)
        self.btn_image.setObjectName("primaryButton" if is_image else "")
        self.btn_text.setObjectName("primaryButton" if not is_image else "")
        self.btn_image.setStyleSheet("")
        self.btn_text.setStyleSheet("")

        self.file_path_field.setVisible(is_image)
        for i in range(self.image_picker_row.count()):
            w = self.image_picker_row.itemAt(i).widget()
            if w:
                w.setVisible(is_image)
        for i in range(self.gif_options_row.count()):
            w = self.gif_options_row.itemAt(i).widget()
            if w:
                w.setVisible(is_image)

        self.text_field.setVisible(not is_image)
        self.fg_color_btn.setVisible(not is_image)
        self.bg_color_btn.setVisible(not is_image)
        self.font_size_spin.setVisible(not is_image)
        if not is_image:
            self._update_text_preview()
        elif self._current_image_path:
            self._show_file_preview(self._current_image_path)

    def _device_native_size(self) -> tuple[int, int]:
        """The panel's native raster, as reported by the service (or the last one seen)."""
        return self.bridge.panel_size()

    def _canvas_size(self) -> tuple[int, int]:
        """The current LOGICAL/oriented canvas size (swapped in Portrait)."""
        w, h = self._device_native_size()
        return logical_canvas_size(w, h, self._orientation)

    def _set_orientation(self, orientation: str):
        self._orientation = orientation
        self.btn_landscape.setChecked(orientation == "landscape")
        self.btn_portrait.setChecked(orientation == "portrait")
        self.btn_landscape.setObjectName("primaryButton" if orientation == "landscape" else "")
        self.btn_portrait.setObjectName("primaryButton" if orientation == "portrait" else "")
        self.btn_landscape.setStyleSheet("")
        self.btn_portrait.setStyleSheet("")
        self._update_dimensions_label()
        self._on_rotation_or_orientation_changed()

    def _on_rotation_or_orientation_changed(self):
        if self.btn_text.isChecked():
            self._update_text_preview()
        elif self._current_image_path and not self._current_image_path.lower().endswith(VIDEO_EXTENSIONS):
            try:
                self.preview.set_image(load_image(self._current_image_path))
            except Exception:
                pass

    def _update_dimensions_label(self):
        w, h = self._canvas_size()
        suffix = "" if self.bridge.connected else " (last known; device not connected)"
        self.dimensions_label.setText(f"{w} × {h} px{suffix}")

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Image, GIF, or Video",
            str(Path.home()),
            "Media (*.png *.jpg *.jpeg *.gif *.bmp *.mp4 *.avi *.mov *.mkv *.webm)"
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str) -> None:
        self._current_image_path = path
        self.file_path_field.setText(Path(path).name)
        self.file_path_field.setToolTip(path)
        self._update_dimensions_label()
        self._show_file_preview(path)

    def _show_file_preview(self, path: str) -> None:
        try:
            img = load_video_thumbnail(path) if path.lower().endswith(VIDEO_EXTENSIONS) else load_image(path)
            self.preview.set_image(img)
        except Exception as e:
            self.log_message.emit(f"[ERROR] Could not preview: {e}")

    def _pick_fg_color(self):
        color = QColorDialog.getColor(self._fg_color, self, "Text Color")
        if color.isValid():
            self._fg_color = color
            self._update_text_preview()

    def _pick_bg_color(self):
        color = QColorDialog.getColor(self._bg_color, self, "Background Color")
        if color.isValid():
            self._bg_color = color
            self._update_text_preview()

    def _update_text_preview(self):
        text = self.text_field.text()
        if not text:
            return
        self._update_dimensions_label()
        fg = (self._fg_color.red(), self._fg_color.green(), self._fg_color.blue())
        bg = (self._bg_color.red(), self._bg_color.green(), self._bg_color.blue())
        w, h = self._canvas_size()
        img = render_text_image(text, w, h, font_size=self.font_size_spin.value(), fg_color=fg, bg_color=bg)
        self.preview.set_image(img)

    def _get_rotation(self) -> int:
        return int(self.rotation_combo.currentText().rstrip("°"))

    # ── Device actions (all through the service) ─────────────

    def _send_to_device(self):
        self._save_settings()
        rotation = self._get_rotation()
        if self.btn_image.isChecked():
            if not self._current_image_path:
                self.log_message.emit("[ERROR] No image/GIF/video selected.")
                return
            path = self._current_image_path
            req = {"cmd": "show", "path": path, "loop": self.loop_combo.currentIndex() == 0,
                   "rotation": rotation, "orientation": self._orientation}
            label = Path(path).name
        else:
            text = self.text_field.text()
            if not text:
                self.log_message.emit("[ERROR] Enter some text first.")
                return
            req = {"cmd": "text", "text": text, "font_size": self.font_size_spin.value(),
                   "fg": list(self._fg_color.getRgb()[:3]), "bg": list(self._bg_color.getRgb()[:3]),
                   "orientation": self._orientation}
            label = f'"{text}"'
        self.send_btn.setEnabled(False)

        def done(reply: dict) -> None:
            self.send_btn.setEnabled(True)
            if reply.get("ok"):
                if reply.get("applied"):
                    self.log_message.emit(f"[OK] Showing {label}")
                else:
                    self.log_message.emit(f"[INFO] Saved {label}; it will show when the cooler connects")
                self.bridge.refresh()

        self.bridge.call(req, done)

    def _stop_display(self):
        def done(reply: dict) -> None:
            if reply.get("ok"):
                self.log_message.emit("[OK] Screen off")
                self.bridge.refresh()

        self.bridge.call({"cmd": "off"}, done)

    # ── Settings ──────────────────────────────────────────────

    def _restore_settings(self) -> None:
        st = self._settings
        st.beginGroup("lcd_page")
        try:
            path = st.value("file", "")
            if path and Path(path).is_file():
                self._set_file(path)
            self.loop_combo.setCurrentIndex(int(st.value("loop_index", 0)))
            self.text_field.setText(st.value("text", ""))
            self.font_size_spin.setValue(int(st.value("font_size", 40)))
            self._fg_color = QColor(st.value("fg", "#ffffff"))
            self._bg_color = QColor(st.value("bg", "#000000"))
            self.rotation_combo.setCurrentIndex(int(st.value("rotation_index", 0)))
            self._set_orientation(st.value("orientation", "landscape"))
            self._switch_source(st.value("source", "image"))
        except (TypeError, ValueError):
            pass
        finally:
            st.endGroup()

    def _save_settings(self) -> None:
        st = self._settings
        st.beginGroup("lcd_page")
        st.setValue("file", self._current_image_path or "")
        st.setValue("loop_index", self.loop_combo.currentIndex())
        st.setValue("text", self.text_field.text())
        st.setValue("font_size", self.font_size_spin.value())
        st.setValue("fg", self._fg_color.name())
        st.setValue("bg", self._bg_color.name())
        st.setValue("rotation_index", self.rotation_combo.currentIndex())
        st.setValue("orientation", self._orientation)
        st.setValue("source", "image" if self.btn_image.isChecked() else "text")
        st.endGroup()

    def hideEvent(self, event):
        self._save_settings()
        super().hideEvent(event)

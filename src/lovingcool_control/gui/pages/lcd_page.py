"""
lovingcool-control :: gui/pages/lcd_page.py
====================================
LCD display editor page — image, GIF, and text → device.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
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
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core.lcd import (
    VIDEO_EXTENSIONS,
    LCDController,
    load_image,
    load_video_thumbnail,
    logical_canvas_size,
    render_text_image,
)

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

    def __init__(self, get_device_callback):
        super().__init__()
        self._get_device = get_device_callback  # callable returning AIODevice | None
        self._lcd_controller: LCDController | None = None
        self._current_image_path: str | None = None
        self._orientation = "landscape"
        self._build_ui()
        self._update_dimensions_label()

        self._size_timer = QTimer(self)
        self._size_timer.setInterval(1000)
        self._size_timer.timeout.connect(self._update_dimensions_label)
        self._size_timer.start()

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

    def _device_native_size(self) -> tuple[int, int]:
        """The LCD panel's fixed physical raster, auto-detected from the connected device."""
        device = self._get_device()
        if device and device.connected:
            return device.panel_size
        return 240, 240

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
        device = self._get_device()
        suffix = "" if (device and device.connected) else " (default — connect device for actual size)"
        self.dimensions_label.setText(f"{w} × {h} px{suffix}")

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Image, GIF, or Video",
            str(Path.home()),
            "Media (*.png *.jpg *.jpeg *.gif *.bmp *.mp4 *.avi *.mov *.mkv *.webm)"
        )
        if path:
            self._current_image_path = path
            self.file_path_field.setText(Path(path).name)
            self._update_dimensions_label()
            try:
                if path.lower().endswith(VIDEO_EXTENSIONS):
                    img = load_video_thumbnail(path)
                else:
                    img = load_image(path)
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
        img = render_text_image(text, w, h, font_size=40, fg_color=fg, bg_color=bg)
        self.preview.set_image(img)

    def _get_rotation(self) -> int:
        return int(self.rotation_combo.currentText().rstrip("°"))

    # ── Device actions ────────────────────────────────────────

    def _ensure_controller(self) -> LCDController | None:
        device = self._get_device()
        if not device or not device.connected:
            self.log_message.emit("[ERROR] No device connected. Connect your AIO first.")
            return None
        self._lcd_controller = LCDController.for_device(device)
        try:
            self._lcd_controller.ensure_info()
        except Exception as e:
            self.log_message.emit(f"[WARN] Could not read device info, using defaults: {e}")
        return self._lcd_controller

    def _send_to_device(self):
        controller = self._ensure_controller()
        if not controller:
            return
        controller.stop_playback()   # a still image/text replaces any GIF, video or live scene

        rotation = self._get_rotation()

        if self.btn_image.isChecked():
            if not self._current_image_path:
                self.log_message.emit("[ERROR] No image/GIF/video selected.")
                return
            path = self._current_image_path
            loop = self.loop_combo.currentText() == "Loop forever"
            try:
                if path.lower().endswith(VIDEO_EXTENSIONS):
                    controller.play_video(path, loop=loop, rotation=rotation, orientation=self._orientation)
                    self.log_message.emit(f"[OK] Playing video: {Path(path).name}")
                elif path.lower().endswith(".gif"):
                    controller.play_gif(path, loop=loop, rotation=rotation, orientation=self._orientation)
                    self.log_message.emit(f"[OK] Playing GIF: {Path(path).name}")
                else:
                    controller.send_image_file(path, rotation=rotation, orientation=self._orientation)
                    self.log_message.emit(f"[OK] Sent image: {Path(path).name}")
            except Exception as e:
                self.log_message.emit(f"[ERROR] Failed to send: {e}")
        else:
            text = self.text_field.text()
            if not text:
                self.log_message.emit("[ERROR] Enter some text first.")
                return
            fg = (self._fg_color.red(), self._fg_color.green(), self._fg_color.blue())
            bg = (self._bg_color.red(), self._bg_color.green(), self._bg_color.blue())
            try:
                controller.send_text(text, fg=fg, bg=bg, orientation=self._orientation)
                self.log_message.emit(f"[OK] Sent text: \"{text}\"")
            except Exception as e:
                self.log_message.emit(f"[ERROR] Failed to send text: {e}")

    def _stop_display(self):
        if self._lcd_controller:
            self._lcd_controller.stop_gif()
            self.log_message.emit("[OK] Stopped playback")

"""
lovingcool-control :: gui/pages/rgb_page.py
====================================
RGB lighting control page.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core.rgb import RGBColor, RGBController, RGBMode

log = logging.getLogger(__name__)


class ColorSwatch(QFrame):
    """Large clickable color preview swatch."""
    clicked = None  # set via signal below

    def __init__(self, color: QColor):
        super().__init__()
        self.setFixedSize(80, 80)
        self._color = color
        self.setStyleSheet(self._style())
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _style(self):
        return (
            f"background-color: {self._color.name()};"
            "border-radius: 12px; border: 2px solid #2C323D;"
        )

    def set_color(self, color: QColor):
        self._color = color
        self.setStyleSheet(self._style())

    def mousePressEvent(self, event):
        if self.clicked:
            self.clicked()


MODE_LABELS = {
    RGBMode.STATIC: "Static Color",
    RGBMode.BREATHING: "Breathing",
    RGBMode.RAINBOW: "Rainbow",
    RGBMode.SPECTRUM_CYCLE: "Spectrum Cycle",
    RGBMode.WAVE: "Wave",
    RGBMode.PULSE: "Pulse",
    RGBMode.OFF: "Off",
}


class RGBPage(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, get_device_callback):
        super().__init__()
        self._get_device = get_device_callback
        self._rgb_controller: RGBController | None = None
        self._current_color = QColor(255, 60, 60)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        title = QLabel("RGB Lighting")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Control the pump head and fan LED lighting")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        content = QHBoxLayout()
        content.setSpacing(24)

        # Left: color picker panel
        color_panel = QFrame()
        color_panel.setObjectName("panel")
        color_layout = QVBoxLayout(color_panel)
        color_layout.setContentsMargins(24, 24, 24, 24)
        color_layout.setSpacing(16)
        color_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        color_label = QLabel("COLOR")
        color_label.setObjectName("sectionLabel")
        color_layout.addWidget(color_label)

        self.swatch = ColorSwatch(self._current_color)
        self.swatch.clicked = self._pick_color
        swatch_row = QHBoxLayout()
        swatch_row.addWidget(self.swatch)
        swatch_row.addStretch()
        color_layout.addLayout(swatch_row)

        pick_btn = QPushButton("Choose Color...")
        pick_btn.clicked.connect(self._pick_color)
        color_layout.addWidget(pick_btn)

        color_layout.addSpacing(8)

        # Quick presets
        preset_label = QLabel("QUICK PRESETS")
        preset_label.setObjectName("sectionLabel")
        color_layout.addWidget(preset_label)

        preset_grid = QGridLayout()
        preset_grid.setSpacing(8)
        presets = [
            ("#FF3C3C", "Red"), ("#3DD6C8", "Cyan"), ("#3C7CFF", "Blue"),
            ("#FFD23C", "Amber"), ("#9B5CFF", "Purple"), ("#3CFF7A", "Green"),
            ("#FF3CB5", "Pink"), ("#FFFFFF", "White"),
        ]
        for idx, (hex_color, name) in enumerate(presets):
            btn = QPushButton()
            btn.setFixedSize(32, 32)
            btn.setStyleSheet(
                f"background-color: {hex_color}; border-radius: 6px; border: 1px solid #2C323D;"
            )
            btn.setToolTip(name)
            btn.clicked.connect(lambda checked, c=hex_color: self._set_color_hex(c))
            preset_grid.addWidget(btn, idx // 4, idx % 4)
        color_layout.addLayout(preset_grid)
        color_layout.addStretch()

        content.addWidget(color_panel, stretch=0)

        # Right: mode & settings panel
        settings_panel = QFrame()
        settings_panel.setObjectName("panel")
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(24, 24, 24, 24)
        settings_layout.setSpacing(16)

        mode_label = QLabel("LIGHTING MODE")
        mode_label.setObjectName("sectionLabel")
        settings_layout.addWidget(mode_label)

        self.mode_combo = QComboBox()
        for mode in MODE_LABELS:
            self.mode_combo.addItem(MODE_LABELS[mode], mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        settings_layout.addWidget(self.mode_combo)

        # Speed slider
        speed_label = QLabel("ANIMATION SPEED")
        speed_label.setObjectName("sectionLabel")
        settings_layout.addWidget(speed_label)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(1, 100)
        self.speed_slider.setValue(50)
        settings_layout.addWidget(self.speed_slider)

        # Brightness slider
        brightness_label = QLabel("BRIGHTNESS")
        brightness_label.setObjectName("sectionLabel")
        settings_layout.addWidget(brightness_label)
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 100)
        self.brightness_slider.setValue(100)
        self.brightness_slider.valueChanged.connect(self._on_brightness_changed)
        settings_layout.addWidget(self.brightness_slider)

        settings_layout.addStretch()

        action_row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primaryButton")
        self.apply_btn.clicked.connect(self._apply_settings)
        self.off_btn = QPushButton("Turn Off")
        self.off_btn.setObjectName("dangerButton")
        self.off_btn.clicked.connect(self._turn_off)
        action_row.addWidget(self.apply_btn)
        action_row.addWidget(self.off_btn)
        settings_layout.addLayout(action_row)

        content.addWidget(settings_panel, stretch=1)
        layout.addLayout(content)

    # ── UI handlers ──────────────────────────────────────────

    def _pick_color(self):
        color = QColorDialog.getColor(self._current_color, self, "Choose RGB Color")
        if color.isValid():
            self._current_color = color
            self.swatch.set_color(color)

    def _set_color_hex(self, hex_color: str):
        self._current_color = QColor(hex_color)
        self.swatch.set_color(self._current_color)

    def _on_mode_changed(self):
        mode = self.mode_combo.currentData()
        is_animated = mode in (RGBMode.BREATHING, RGBMode.RAINBOW,
                                RGBMode.SPECTRUM_CYCLE, RGBMode.WAVE, RGBMode.PULSE)
        self.speed_slider.setEnabled(is_animated)

    def _on_brightness_changed(self, value: int):
        if self._rgb_controller:
            try:
                self._rgb_controller.set_brightness(value)
            except Exception as e:
                self.log_message.emit(f"[ERROR] {e}")

    # ── Device actions ───────────────────────────────────────

    def _ensure_controller(self) -> RGBController | None:
        device = self._get_device()
        if not device or not device.connected:
            self.log_message.emit("[ERROR] No device connected. Connect your AIO first.")
            return None
        if not self._rgb_controller or self._rgb_controller.device != device:
            self._rgb_controller = RGBController(device)
        return self._rgb_controller

    def _apply_settings(self):
        controller = self._ensure_controller()
        if not controller:
            return

        mode = self.mode_combo.currentData()
        color = RGBColor(
            self._current_color.red(),
            self._current_color.green(),
            self._current_color.blue(),
        )
        speed = self.speed_slider.value()
        brightness = self.brightness_slider.value()

        try:
            if mode == RGBMode.STATIC:
                controller.set_static(color, brightness)
            elif mode == RGBMode.OFF:
                controller.turn_off()
            else:
                controller.set_mode(mode, color, speed, brightness)
            self.log_message.emit(f"[OK] RGB set: {MODE_LABELS[mode]} — {color.to_hex()}")
        except Exception as e:
            self.log_message.emit(f"[ERROR] Failed to set RGB: {e}")

    def _turn_off(self):
        controller = self._ensure_controller()
        if not controller:
            return
        try:
            controller.turn_off()
            self.log_message.emit("[OK] RGB turned off")
        except Exception as e:
            self.log_message.emit(f"[ERROR] {e}")

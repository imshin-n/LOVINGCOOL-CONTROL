"""
lovingcool-control :: gui/pages/device_page.py
========================================
Device discovery and connection page — this is what users see first.
Since the protocol isn't reverse-engineered yet, this page doubles as
a diagnostic tool to help identify the AIO's VID:PID.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core.device import AIODevice, DeviceError, find_any_cooler_candidates, find_devices
from lovingcool_control.core.lcd import LCDController

log = logging.getLogger(__name__)


class DevicePage(QWidget):
    device_connected = pyqtSignal(object)     # emits AIODevice
    device_disconnected = pyqtSignal()
    log_message = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._current_device: AIODevice | None = None
        self._build_ui()
        self.refresh_devices()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        title = QLabel("Device Connection")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Connect to your Lovi.Cool AIO controller")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        content = QHBoxLayout()
        content.setSpacing(24)

        # Known devices panel
        known_panel = QFrame()
        known_panel.setObjectName("panel")
        known_layout = QVBoxLayout(known_panel)
        known_layout.setContentsMargins(24, 24, 24, 24)
        known_layout.setSpacing(12)

        known_label = QLabel("RECOGNIZED DEVICES")
        known_label.setObjectName("sectionLabel")
        known_layout.addWidget(known_label)

        self.known_list = QListWidget()
        self.known_list.setMinimumHeight(160)
        known_layout.addWidget(self.known_list)

        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primaryButton")
        self.connect_btn.clicked.connect(self._connect_selected)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        btn_row.addWidget(self.connect_btn)
        btn_row.addWidget(self.disconnect_btn)
        btn_row.addWidget(self.refresh_btn)
        known_layout.addLayout(btn_row)

        content.addWidget(known_panel, stretch=1)

        # Candidates panel (for protocol discovery)
        candidates_panel = QFrame()
        candidates_panel.setObjectName("panel")
        candidates_layout = QVBoxLayout(candidates_panel)
        candidates_layout.setContentsMargins(24, 24, 24, 24)
        candidates_layout.setSpacing(12)

        candidates_label = QLabel("UNRECOGNIZED USB DEVICES (possible AIO)")
        candidates_label.setObjectName("sectionLabel")
        candidates_layout.addWidget(candidates_label)

        candidates_hint = QLabel(
            "If your AIO isn't recognized, it may show up here. Note its "
            "VID:PID and add it to core/device.py — see the setup guide."
        )
        candidates_hint.setObjectName("pageSubtitle")
        candidates_hint.setWordWrap(True)
        candidates_layout.addWidget(candidates_hint)

        self.candidates_list = QListWidget()
        self.candidates_list.setMinimumHeight(160)
        candidates_layout.addWidget(self.candidates_list)

        content.addWidget(candidates_panel, stretch=1)
        layout.addLayout(content)

        # Status / details panel
        details_panel = QFrame()
        details_panel.setObjectName("panel")
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(24, 24, 24, 24)

        details_label = QLabel("CONNECTION STATUS")
        details_label.setObjectName("sectionLabel")
        details_layout.addWidget(details_label)

        self.status_text = QTextEdit()
        self.status_text.setObjectName("logConsole")
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(120)
        self.status_text.setText("No device connected.")
        details_layout.addWidget(self.status_text)

        layout.addWidget(details_panel)
        layout.addStretch()

    # ── Device discovery ─────────────────────────────────────

    def refresh_devices(self):
        self.known_list.clear()
        self.candidates_list.clear()

        known = find_devices()
        if known:
            for dev in known:
                item = QListWidgetItem(
                    f"{dev.spec.name}   [0x{dev.spec.vid:04X}:0x{dev.spec.pid:04X}]"
                )
                item.setData(Qt.ItemDataRole.UserRole, dev)
                self.known_list.addItem(item)
        else:
            self.known_list.addItem("No recognized devices found.")

        candidates = find_any_cooler_candidates()
        if candidates:
            for c in candidates:
                vid, pid = c["vendor_id"], c["product_id"]
                prod = c.get("product_string") or "(unnamed)"
                self.candidates_list.addItem(
                    f"{prod}   [0x{vid:04X}:0x{pid:04X}]   score={c['_score']}"
                )
        else:
            self.candidates_list.addItem("No unrecognized candidates detected.")

        self.log_message.emit("[INFO] Device list refreshed")

    def _connect_selected(self):
        item = self.known_list.currentItem()
        if not item:
            self.log_message.emit("[ERROR] Select a device from the list first.")
            return
        dev: AIODevice = item.data(Qt.ItemDataRole.UserRole)
        if not dev:
            self.log_message.emit("[ERROR] No real device selected (placeholder VID:PID is 0x0000).")
            self.status_text.setText(
                "No real device configured yet.\n\n"
                "Run: uv run python tools/enumerate.py\n"
                "Then add the VID:PID to devices.toml (see core/devices.toml)."
            )
            return
        try:
            dev.connect()
            info_line = ""
            if dev.spec.transport == "serial":
                try:
                    info = LCDController.for_device(dev).refresh_info()
                    info_line = f"Model: {info.get('model', '?')}  firmware {info.get('version', '?')}\n"
                except DeviceError as e:
                    self.log_message.emit(f"[WARN] Connected, but the device didn't answer get_info: {e}")
            self._current_device = dev
            self.device_connected.emit(dev)
            width, height = dev.panel_size
            self.status_text.setText(
                f"Connected: {dev.spec.name}\n"
                f"VID:PID = 0x{dev.spec.vid:04X}:0x{dev.spec.pid:04X}\n"
                f"{info_line}"
                f"LCD: {width}x{height} {dev.spec.lcd_color_format}"
            )
            self.log_message.emit(f"[OK] Connected to {dev.spec.name}")
        except DeviceError as e:
            self.log_message.emit(f"[ERROR] {e}")
            self.status_text.setText(f"Connection failed: {e}")

    def _disconnect(self):
        if self._current_device:
            LCDController.for_device(self._current_device).stop_playback()
            self._current_device.disconnect()
            self._current_device = None
            self.device_disconnected.emit()
            self.status_text.setText("Disconnected.")
            self.log_message.emit("[OK] Device disconnected")

    def get_current_device(self) -> AIODevice | None:
        return self._current_device

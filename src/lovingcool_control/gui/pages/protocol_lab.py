"""
lovingcool-control :: gui/pages/protocol_lab.py
=========================================
Raw packet sender / protocol exploration UI.
Used during reverse engineering to send arbitrary hex payloads
and inspect device responses — without needing the command line.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


# Saved payloads to try common probe sequences
QUICK_PAYLOADS = {
    "Zeros (64 bytes)":       "00 " * 64,
    "Get Firmware (guess)":   "10 00 " + "00 " * 62,
    "Get Status (guess)":     "20 00 " + "00 " * 62,
    "LCD Init (guess)":       "30 01 00 F0 00 F0 " + "00 " * 58,
    "RGB Static Red (guess)": "40 FF 01 FF 00 00 50 64 " + "00 " * 56,
    "RGB Off (guess)":        "40 FF 00 00 00 00 00 00 " + "00 " * 56,
    "Custom...":              "",
}


class PacketLog(QTextEdit):
    """Monospaced, colour-coded TX/RX packet display."""
    def __init__(self):
        super().__init__()
        self.setObjectName("logConsole")
        self.setReadOnly(True)
        self.setMinimumHeight(280)

    def log_tx(self, data: bytes):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        hex_str = self._hexdump(data)
        html = (
            f'<span style="color:#8B93A1">[{ts}]</span> '
            f'<span style="color:#E8A33D; font-weight:bold">→ TX ({len(data)}b)</span><br>'
            f'<span style="color:#3DD6C8">{hex_str}</span><br>'
        )
        self.append(html)
        self.moveCursor(QTextCursor.MoveOperation.End)

    def log_rx(self, data: bytes):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        hex_str = self._hexdump(data)
        html = (
            f'<span style="color:#8B93A1">[{ts}]</span> '
            f'<span style="color:#9B77FF; font-weight:bold">← RX ({len(data)}b)</span><br>'
            f'<span style="color:#E8EBF0">{hex_str}</span><br>'
        )
        self.append(html)
        self.moveCursor(QTextCursor.MoveOperation.End)

    def log_info(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.append(
            f'<span style="color:#8B93A1">[{ts}] {msg}</span><br>'
        )
        self.moveCursor(QTextCursor.MoveOperation.End)

    def log_error(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.append(
            f'<span style="color:#E0556B">[{ts}] ERROR: {msg}</span><br>'
        )
        self.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _hexdump(data: bytes) -> str:
        rows = []
        for i in range(0, len(data), 16):
            chunk = data[i:i+16]
            hex_part = "&nbsp;".join(f"{b:02X}" for b in chunk)
            rows.append(f"&nbsp;&nbsp;{i:04X}&nbsp;&nbsp;{hex_part}")
        return "<br>".join(rows)


class ReadPoller(QThread):
    """Background thread that continuously polls the device for incoming data."""
    packet_received = pyqtSignal(bytes)

    def __init__(self, get_device_cb):
        super().__init__()
        self._get_device = get_device_cb
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            device = self._get_device()
            if device and device.connected:
                try:
                    data = device.read_packet(timeout_ms=100)
                    if data:
                        self.packet_received.emit(data)
                except Exception:
                    pass
            else:
                self.msleep(200)

    def stop(self):
        self._running = False
        self.wait(2000)


class ProtocolLabPage(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, get_device_callback):
        super().__init__()
        self._get_device = get_device_callback
        self._poller: ReadPoller | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        # Header
        title = QLabel("Protocol Lab")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Send raw USB HID packets and observe device responses — "
            "for protocol reverse engineering"
        )
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # Warning banner
        warn = QFrame()
        warn.setObjectName("panel")
        warn.setStyleSheet(
            "QFrame#panel { border: 1px solid #E8A33D; background: #1F1A10; border-radius: 8px; }"
        )
        warn_layout = QHBoxLayout(warn)
        warn_layout.setContentsMargins(16, 12, 16, 12)
        warn_icon = QLabel("⚠")
        warn_icon.setStyleSheet("color: #E8A33D; font-size: 20px;")
        warn_text = QLabel(
            "Sending unknown commands to hardware can cause unexpected behaviour. "
            "Start with non-destructive read/status queries. Never send random data "
            "to flash-programming or firmware-update command ranges."
        )
        warn_text.setWordWrap(True)
        warn_text.setStyleSheet("color: #E8A33D; font-size: 12px;")
        warn_layout.addWidget(warn_icon)
        warn_layout.addWidget(warn_text, stretch=1)
        layout.addWidget(warn)

        # Sender panel
        send_panel = QFrame()
        send_panel.setObjectName("panel")
        send_layout = QVBoxLayout(send_panel)
        send_layout.setContentsMargins(24, 24, 24, 24)
        send_layout.setSpacing(12)

        top_row = QHBoxLayout()

        # Quick presets dropdown
        self.preset_combo = QComboBox()
        for label in QUICK_PAYLOADS:
            self.preset_combo.addItem(label)
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        top_row.addWidget(QLabel("Preset:"))
        top_row.addWidget(self.preset_combo, stretch=1)
        send_layout.addLayout(top_row)

        # Hex payload input
        payload_label = QLabel("HEX PAYLOAD (space-separated bytes)")
        payload_label.setObjectName("sectionLabel")
        send_layout.addWidget(payload_label)

        self.hex_input = QLineEdit()
        self.hex_input.setPlaceholderText("e.g.  10 00 00 00 00 ...")
        self.hex_input.setStyleSheet(
            "font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12px;"
        )
        send_layout.addWidget(self.hex_input)

        options_row = QHBoxLayout()
        self.repeat_check = QCheckBox("Repeat every")
        self.repeat_interval = QSpinBox()
        self.repeat_interval.setRange(50, 10000)
        self.repeat_interval.setValue(1000)
        self.repeat_interval.setSuffix(" ms")
        self.repeat_check.stateChanged.connect(
            lambda s: self.repeat_interval.setEnabled(bool(s))
        )
        self.repeat_interval.setEnabled(False)
        self.read_response = QCheckBox("Read response after send")
        self.read_response.setChecked(True)
        options_row.addWidget(self.repeat_check)
        options_row.addWidget(self.repeat_interval)
        options_row.addSpacing(20)
        options_row.addWidget(self.read_response)
        options_row.addStretch()
        send_layout.addLayout(options_row)

        action_row = QHBoxLayout()
        self.send_btn = QPushButton("Send Packet")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.clicked.connect(self._send_packet)

        self.poll_btn = QPushButton("Start RX Poller")
        self.poll_btn.setCheckable(True)
        self.poll_btn.clicked.connect(self._toggle_poller)

        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(lambda: self.packet_log.clear())

        action_row.addWidget(self.send_btn)
        action_row.addWidget(self.poll_btn)
        action_row.addWidget(clear_btn)
        action_row.addStretch()
        send_layout.addLayout(action_row)

        layout.addWidget(send_panel)

        # Packet log
        log_label = QLabel("PACKET LOG")
        log_label.setObjectName("sectionLabel")
        layout.addWidget(log_label)

        self.packet_log = PacketLog()
        layout.addWidget(self.packet_log)
        self.packet_log.log_info("Ready. Connect a device and send packets.")

    # ── UI handlers ──────────────────────────────────────────

    def _on_preset_changed(self, label: str):
        payload = QUICK_PAYLOADS.get(label, "")
        if payload:
            self.hex_input.setText(payload.strip())

    def _parse_hex_input(self) -> bytes | None:
        text = self.hex_input.text().strip()
        if not text:
            return None
        try:
            return bytes(int(x, 16) for x in text.split())
        except ValueError as e:
            self.packet_log.log_error(f"Invalid hex input: {e}")
            return None

    def _send_packet(self):
        device = self._get_device()
        if not device or not device.connected:
            self.packet_log.log_error("No device connected.")
            return

        data = self._parse_hex_input()
        if data is None:
            return

        # Pad to 64 bytes
        if len(data) < 64:
            data += bytes(64 - len(data))
        else:
            data = data[:64]

        try:
            self.packet_log.log_tx(data)
            device.send_packet(data)

            if self.read_response.isChecked():
                response = device.read_packet(timeout_ms=300)
                if response:
                    self.packet_log.log_rx(response)
                else:
                    self.packet_log.log_info("(no response within 300 ms)")
        except Exception as e:
            self.packet_log.log_error(str(e))
            self.log_message.emit(f"[ERROR] {e}")

    def _toggle_poller(self, checked: bool):
        if checked:
            self._start_poller()
            self.poll_btn.setText("Stop RX Poller")
        else:
            self._stop_poller()
            self.poll_btn.setText("Start RX Poller")

    def _start_poller(self):
        if self._poller and self._poller.isRunning():
            return
        self._poller = ReadPoller(self._get_device)
        self._poller.packet_received.connect(self.packet_log.log_rx)
        self._poller.start()
        self.packet_log.log_info("RX poller started — watching for device-initiated packets.")

    def _stop_poller(self):
        if self._poller:
            self._poller.stop()
            self._poller = None
        self.packet_log.log_info("RX poller stopped.")

    def hideEvent(self, event):
        """Stop the poller when navigating away from this page."""
        self._stop_poller()
        self.poll_btn.setChecked(False)
        self.poll_btn.setText("Start RX Poller")
        super().hideEvent(event)

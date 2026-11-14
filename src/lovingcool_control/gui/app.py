"""
lovingcool-control :: gui/app.py
=========================
Main application window.

    lovingcool-control            (installed entry point)
    python -m lovingcool_control   (from a checkout, after `uv sync`)
"""

from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.gui.pages.device_page import DevicePage
from lovingcool_control.gui.pages.lcd_page import LCDPage
from lovingcool_control.gui.pages.protocol_lab import ProtocolLabPage
from lovingcool_control.gui.pages.rgb_page import RGBPage
from lovingcool_control.gui.pages.screen_designer import ScreenDesignerPage
from lovingcool_control.gui.theme import DANGER, STYLESHEET, SUCCESS
from lovingcool_control.gui.widgets.log_console import LogConsole

log = logging.getLogger("lovingcool-control")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LOVINGCOOL-CONTROL — AIO screen controller")
        self.resize(1150, 760)
        self.setMinimumSize(900, 620)

        self._current_device = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Main row: sidebar + pages ────────────────────────
        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(0)

        # Sidebar
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        brand_title = QLabel("LOVINGCOOL CONTROL")
        brand_title.setObjectName("brandTitle")
        brand_subtitle = QLabel("Open AIO Control")
        brand_subtitle.setObjectName("brandSubtitle")
        sidebar_layout.addWidget(brand_title)
        sidebar_layout.addWidget(brand_subtitle)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}

        nav_items = [
            ("device",   "⬡  Device"),
            ("lcd",      "▣  LCD Display"),
            ("screen",   "🖥  Screen Designer"),
            ("rgb",      "◐  RGB Lighting"),
            ("protocol", "⌬  Protocol Lab"),
        ]
        for key, label in nav_items:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setFixedHeight(44)
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()

        # Status dot in sidebar footer
        status_bar = QWidget()
        status_bar.setObjectName("statusBar")
        status_layout = QVBoxLayout(status_bar)
        status_layout.setContentsMargins(16, 12, 16, 12)
        status_row = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setStyleSheet(f"color: {DANGER};")
        self.status_label = QLabel("Disconnected")
        self.status_label.setObjectName("statusText")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_layout.addLayout(status_row)
        sidebar_layout.addWidget(status_bar)

        main_row.addWidget(sidebar)

        # Page stack
        self.stack = QStackedWidget()
        self.device_page = DevicePage()
        self.lcd_page = LCDPage(self.get_current_device)
        self.screen_page = ScreenDesignerPage(self.get_current_device)
        self.rgb_page = RGBPage(self.get_current_device)
        self.protocol_page = ProtocolLabPage(self.get_current_device)

        self.pages = {
            "device":   self.device_page,
            "lcd":      self.lcd_page,
            "screen":   self.screen_page,
            "rgb":      self.rgb_page,
            "protocol": self.protocol_page,
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        main_row.addWidget(self.stack, stretch=1)
        outer.addLayout(main_row, stretch=1)

        # ── Global log console at the very bottom ────────────
        self.log_console = LogConsole("Event Log")
        self.log_console.setContentsMargins(16, 0, 16, 12)
        outer.addWidget(self.log_console)

        # ── Wire navigation ──────────────────────────────────
        for key, btn in self.nav_buttons.items():
            btn.clicked.connect(lambda checked, k=key: self._switch_page(k))
        self.nav_buttons["device"].setChecked(True)
        self.stack.setCurrentWidget(self.device_page)

        self.device_page.device_connected.connect(self._on_device_connected)
        self.device_page.device_disconnected.connect(self._on_device_disconnected)

        for page in self.pages.values():
            if hasattr(page, "log_message"):
                page.log_message.connect(self._log)

        self.setStyleSheet(STYLESHEET)

    # ── Navigation ───────────────────────────────────────────

    def _switch_page(self, key: str):
        self.stack.setCurrentWidget(self.pages[key])

    def get_current_device(self):
        return self._current_device

    def _on_device_connected(self, device):
        self._current_device = device
        self.status_dot.setStyleSheet(f"color: {SUCCESS};")
        self.status_label.setText(device.spec.name)
        self._log(f"[OK] Connected: {device.spec.name} (0x{device.spec.vid:04X}:0x{device.spec.pid:04X})")

    def _on_device_disconnected(self):
        self._current_device = None
        self.status_dot.setStyleSheet(f"color: {DANGER};")
        self.status_label.setText("Disconnected")
        self._log("[INFO] Device disconnected")

    def _log(self, message: str):
        self.log_console.append(message)
        log.info(message)

    def closeEvent(self, event):
        if self.screen_page._scene_player:
            self.screen_page._scene_player.stop()
        if self._current_device:
            self._current_device.disconnect()
        event.accept()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("lovingcool-control")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

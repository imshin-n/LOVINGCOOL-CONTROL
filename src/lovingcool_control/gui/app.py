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

from PyQt6.QtCore import QSettings
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

from lovingcool_control.gui.bridge import MODE_EMBEDDED, MODE_NONE, ServiceBridge
from lovingcool_control.gui.pages.designer import ScreenDesignerPage
from lovingcool_control.gui.pages.lcd_page import LCDPage
from lovingcool_control.gui.pages.status_page import StatusPage
from lovingcool_control.gui.theme import ACCENT_AMBER, DANGER, STYLESHEET, SUCCESS
from lovingcool_control.gui.widgets.log_console import LogConsole

log = logging.getLogger("lovingcool-control")


class MainWindow(QMainWindow):
    def __init__(self, bridge: ServiceBridge | None = None):
        super().__init__()
        self.setWindowTitle("LOVINGCOOL-CONTROL — AIO screen controller")
        self.resize(1150, 760)
        self.setMinimumSize(900, 620)
        self._settings = QSettings("lovingcool-control", "lovingcool-control")
        self.bridge = bridge or ServiceBridge()

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
            ("status",   "⬡  Status"),
            ("lcd",      "▣  LCD Display"),
            ("screen",   "🖥  Screen Designer"),
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
        self.status_label = QLabel("Starting…")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusText")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_layout.addLayout(status_row)
        sidebar_layout.addWidget(status_bar)

        main_row.addWidget(sidebar)

        # Page stack
        self.stack = QStackedWidget()
        self.status_page = StatusPage(self.bridge)
        self.lcd_page = LCDPage(self.bridge)
        self.screen_page = ScreenDesignerPage(self.bridge)

        self.pages = {
            "status": self.status_page,
            "lcd":    self.lcd_page,
            "screen": self.screen_page,
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
        last = self._settings.value("window/page", "status")
        self._switch_page(last if last in self.pages else "status")
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        for page in self.pages.values():
            page.log_message.connect(self._log)
        self.bridge.log_message.connect(self._log)
        self.bridge.status_changed.connect(self._on_status)

        self.setStyleSheet(STYLESHEET)
        self.bridge.start()

    # ── Navigation ───────────────────────────────────────────

    def _switch_page(self, key: str):
        self.nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])

    def _on_status(self, st: dict):
        dev = st.get("device") if st.get("connected") else None
        if self.bridge.mode == MODE_NONE:
            color, text = DANGER, "No service"
        elif dev:
            color, text = SUCCESS, dev["name"]
        else:
            color, text = ACCENT_AMBER, "Waiting for cooler…"
        if self.bridge.mode == MODE_EMBEDDED:
            text += "\n(built-in service)"
        self.status_dot.setStyleSheet(f"color: {color};")
        self.status_label.setText(text)

    def _log(self, message: str):
        self.log_console.append(message)
        log.info(message)

    def closeEvent(self, event):
        current = next((k for k, page in self.pages.items() if page is self.stack.currentWidget()), "status")
        self._settings.setValue("window/page", current)
        self._settings.setValue("window/geometry", self.saveGeometry())
        self.lcd_page._save_settings()
        # With the background service the screen keeps going; a built-in one stops here.
        self.bridge.shutdown()
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

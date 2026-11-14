"""
lovingcool-control :: gui/app.py
================================
Main window, laid out like a desktop RGB-controller app: tabs across the
top (Devices, Screen Designer, Information, Settings), each device as a
tab down the left side, and a bottom bar with saved scenes and the
Screen Off / Rescan Devices buttons.

    lovingcool-control              (installed entry point)
    python -m lovingcool_control    (from a checkout, after `uv sync`)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core.canvas import save_scene
from lovingcool_control.gui.bridge import MODE_EMBEDDED, MODE_NONE, ServiceBridge
from lovingcool_control.gui.pages.designer import ScreenDesignerPage
from lovingcool_control.gui.pages.device_view import DeviceView, saved_scenes, scenes_dir
from lovingcool_control.gui.pages.info_page import InfoPage
from lovingcool_control.gui.pages.settings_page import SettingsPage
from lovingcool_control.gui.theme import DANGER, SUCCESS, WARNING, apply_theme
from lovingcool_control.gui.widgets.side_tabs import side_tabs

log = logging.getLogger("lovingcool-control")

ICON_PATH = Path(__file__).with_name("icon.svg")


class NoDevicePage(QWidget):
    def __init__(self, on_rescan):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label)
        btn = QPushButton("Rescan Devices")
        btn.clicked.connect(on_rescan)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addStretch()
        self.set_error(None)

    def set_error(self, error: str | None) -> None:
        text = ("<b>No supported cooler found.</b><br><br>"
                "Check that the cooler's USB cable is connected to the motherboard. "
                "If it's a model this app doesn't know yet, the Information → USB Devices tab "
                "lists candidates; see the README on adding one.")
        if error:
            text += f"<br><br>Last error: {error}"
        self.label.setText(text)


class DevicesTab(QWidget):
    """Side tabs, one per connected device (or a placeholder)."""

    def __init__(self, bridge: ServiceBridge, view: DeviceView, on_rescan):
        super().__init__()
        self.bridge, self.view = bridge, view
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = side_tabs()
        lay.addWidget(self.tabs)
        self.placeholder = NoDevicePage(on_rescan)
        self._shown: str | None = None
        self._icon = QIcon(str(ICON_PATH)) if ICON_PATH.is_file() else QIcon()
        bridge.status_changed.connect(self._on_status)
        self._show(None)

    def _show(self, name: str | None) -> None:
        if name == self._shown and self.tabs.count():
            return
        self.tabs.clear()
        if name:
            self.tabs.addTab(self.view, self._icon, name)
        else:
            self.tabs.addTab(self.placeholder, "No device")
        self._shown = name

    def _on_status(self, st: dict) -> None:
        dev = st.get("device") if st.get("connected") else None
        self._show(dev["name"] if dev else None)
        if not dev:
            self.placeholder.set_error(st.get("error"))


class MainWindow(QMainWindow):
    def __init__(self, bridge: ServiceBridge | None = None):
        super().__init__()
        self.setWindowTitle("LOVINGCOOL-CONTROL")
        self.resize(1000, 640)
        self.setMinimumSize(820, 520)
        self._settings = QSettings("lovingcool-control", "lovingcool-control")
        self.bridge = bridge or ServiceBridge()

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # ── Pages ────────────────────────────────────────────
        self.device_view = DeviceView(self.bridge)
        self.devices_tab = DevicesTab(self.bridge, self.device_view, self.rescan)
        self.designer = ScreenDesignerPage(self.bridge)
        self.info = InfoPage(self.bridge)
        self.settings_page = SettingsPage()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.devices_tab, "Devices")
        self.tabs.addTab(self.designer, "Screen Designer")
        self.tabs.addTab(self.info, "Information")
        self.tabs.addTab(self.settings_page, "Settings")
        outer.addWidget(self.tabs, stretch=1)

        # ── Bottom bar: saved scenes on the left, actions on the right ──
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Scene:"))
        self.scene_combo = QComboBox()
        self.scene_combo.setMinimumWidth(180)
        bar.addWidget(self.scene_combo)
        self.btn_load_scene = QPushButton("Show Scene")
        self.btn_load_scene.clicked.connect(self._show_scene)
        self.btn_save_scene = QPushButton("Save Scene")
        self.btn_save_scene.setToolTip("Save the Screen Designer's current scene")
        self.btn_save_scene.clicked.connect(self._save_scene)
        bar.addWidget(self.btn_load_scene)
        bar.addWidget(self.btn_save_scene)
        bar.addStretch()
        self.status_dot = QLabel("●")
        self.status_label = QLabel("Starting…")
        bar.addWidget(self.status_dot)
        bar.addWidget(self.status_label)
        bar.addSpacing(12)
        self.btn_off = QPushButton("Screen Off")
        self.btn_off.clicked.connect(lambda: self.bridge.call({"cmd": "off"}, self._after_action))
        self.btn_rescan = QPushButton("Rescan Devices")
        self.btn_rescan.clicked.connect(self.rescan)
        bar.addWidget(self.btn_off)
        bar.addWidget(self.btn_rescan)
        outer.addLayout(bar)

        # ── Wiring ───────────────────────────────────────────
        for src in (self.device_view, self.designer):
            src.log_message.connect(self._log)
        self.bridge.log_message.connect(self._log)
        self.bridge.status_changed.connect(self._on_status)
        self.device_view.edit_scene_requested.connect(lambda: self.tabs.setCurrentWidget(self.designer))
        self.settings_page.theme_changed.connect(self._apply_theme)
        self.tabs.currentChanged.connect(lambda _i: self.refresh_scenes())

        self.refresh_scenes()
        self.tabs.setCurrentIndex(int(self._settings.value("window/tab", 0)))
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.bridge.start()

    # ── Bottom bar ───────────────────────────────────────────

    def refresh_scenes(self) -> None:
        current = self.scene_combo.currentData()
        self.scene_combo.clear()
        for p in saved_scenes():
            self.scene_combo.addItem(p.stem, str(p))
        has = self.scene_combo.count() > 0
        if has:
            self.scene_combo.setCurrentIndex(max(0, self.scene_combo.findData(current)))
        else:
            self.scene_combo.addItem("(no saved scenes)")
        self.scene_combo.setEnabled(has)
        self.btn_load_scene.setEnabled(has)
        self.device_view.refresh_scenes()

    def _show_scene(self) -> None:
        path = self.scene_combo.currentData()
        if path:
            self.bridge.call({"cmd": "scene", "path": path}, self._after_action)
            self._log(f"[INFO] Showing scene {Path(path).stem}")

    def _save_scene(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Scene", "Name for the Screen Designer's current scene:")
        name = name.strip().replace("/", "-")
        if not ok or not name:
            return
        path = scenes_dir() / f"{name}.json"
        scenes_dir().mkdir(parents=True, exist_ok=True)
        save_scene(self.designer._build_scene_config(), path)
        self._log(f"[OK] Saved scene {name}")
        self.refresh_scenes()
        self.scene_combo.setCurrentIndex(max(0, self.scene_combo.findData(str(path))))

    def rescan(self) -> None:
        self._log("[INFO] Rescanning devices…")
        self.info.refresh_candidates()
        self.bridge.call({"cmd": "reconnect"}, self._after_action)

    def _after_action(self, reply: dict) -> None:
        if reply.get("ok"):
            self.bridge.refresh()

    # ── Status, log, theme ───────────────────────────────────

    def _on_status(self, st: dict) -> None:
        dev = st.get("device") if st.get("connected") else None
        if self.bridge.mode == MODE_NONE:
            color, text = DANGER, "No service"
        elif dev:
            color, text = SUCCESS, f"Connected: {dev['name']}"
        else:
            color, text = WARNING, "Waiting for a cooler…"
        if self.bridge.mode == MODE_EMBEDDED:
            text += " (built-in service)"
        self.status_dot.setStyleSheet(f"color: {color};")
        self.status_label.setText(text)
        enabled = self.bridge.mode != MODE_NONE
        for b in (self.btn_off, self.btn_rescan):
            b.setEnabled(enabled)

    def _log(self, message: str) -> None:
        self.info.log.append(message)
        log.info(message)
        if message.startswith("[ERROR]"):
            self.statusBar().showMessage(message.removeprefix("[ERROR] "), 8000)

    def _apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)

    def closeEvent(self, event):
        self._settings.setValue("window/tab", self.tabs.currentIndex())
        self._settings.setValue("window/geometry", self.saveGeometry())
        self.device_view._save_settings()
        # With the background service the screen keeps going; a built-in one stops here.
        self.bridge.shutdown()
        event.accept()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("lovingcool-control")
    app.setDesktopFileName("lovingcool-control")   # Wayland: match the .desktop entry and its icon
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    apply_theme(app, QSettings("lovingcool-control", "lovingcool-control").value("ui/theme", "auto"))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

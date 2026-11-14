"""Information tab: device details, the service, other USB devices, and the event log (side tabs)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control import __version__
from lovingcool_control.core.device import KNOWN_DEVICES, find_any_cooler_candidates, user_config_dir
from lovingcool_control.gui.bridge import MODE_EMBEDDED, MODE_SERVICE, ServiceBridge
from lovingcool_control.gui.widgets.log_console import LogConsole
from lovingcool_control.gui.widgets.side_tabs import side_tabs

MODE_TEXT = {
    MODE_SERVICE: "Background service (lovingcool-controld). The screen keeps running when this window closes.",
    MODE_EMBEDDED: ("Built into this window: the screen stops when you close it. Install the background "
                    "service to keep it running."),
}


def _selectable(text: str = "—") -> QLabel:
    lbl = QLabel(text)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setWordWrap(True)
    return lbl


class InfoPage(QWidget):
    def __init__(self, bridge: ServiceBridge):
        super().__init__()
        self.bridge = bridge
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = side_tabs()
        root.addWidget(self.tabs)

        # Device
        dev = QWidget()
        f = QFormLayout(dev)
        f.setContentsMargins(16, 16, 16, 16)
        self.fields = {k: _selectable() for k in ("Name", "Model", "Firmware", "USB ID", "Panel", "Showing", "Error")}
        for k, lbl in self.fields.items():
            f.addRow(f"{k}:", lbl)
        self.tabs.addTab(dev, "Device")

        # Service
        svc = QWidget()
        f2 = QFormLayout(svc)
        f2.setContentsMargins(16, 16, 16, 16)
        self.lbl_mode = _selectable("Starting…")
        f2.addRow("Running as:", self.lbl_mode)
        f2.addRow("Socket:", _selectable(str(bridge.socket_path)))
        f2.addRow("Settings:", _selectable(str(user_config_dir())))
        f2.addRow("Version:", _selectable(__version__))
        f2.addRow("Command line:", _selectable("lovingcool-ctl --help"))
        self.tabs.addTab(svc, "Service")

        # Other USB devices
        usb = QWidget()
        ul = QVBoxLayout(usb)
        ul.setContentsMargins(16, 16, 16, 16)
        hint = QLabel("Devices that might be a cooler controller but aren't in the device list. "
                      "Add the VID:PID to devices.toml to try one (see the README).")
        hint.setWordWrap(True)
        ul.addWidget(hint)
        self.candidates = QListWidget()
        ul.addWidget(self.candidates)
        rescan = QPushButton("Rescan USB")
        rescan.clicked.connect(self.refresh_candidates)
        ul.addWidget(rescan, alignment=Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(usb, "USB Devices")

        # Log
        log_page = QWidget()
        ll = QVBoxLayout(log_page)
        ll.setContentsMargins(12, 12, 12, 12)
        self.log = LogConsole("Event log")
        ll.addWidget(self.log)
        self.tabs.addTab(log_page, "Log")

        bridge.status_changed.connect(self._on_status)
        bridge.mode_changed.connect(lambda _m: self._on_status(bridge.status))
        self.refresh_candidates()

    def _on_status(self, st: dict) -> None:
        self.lbl_mode.setText(MODE_TEXT.get(self.bridge.mode, "Not running: no service could be reached or started."))
        dev = st.get("device") if st.get("connected") else None
        values = {
            "Name": dev["name"] if dev else "Not connected",
            "Model": (dev or {}).get("model") or "—",
            "Firmware": (dev or {}).get("firmware") or "—",
            "USB ID": (dev or {}).get("usb_id") or "—",
            "Panel": " × ".join(str(n) for n in (dev or {}).get("panel") or []) or "—",
            "Showing": (st.get("content") or "nothing yet") + ("  (playing)" if st.get("playing") else ""),
            "Error": st.get("error") or "none",
        }
        for k, v in values.items():
            self.fields[k].setText(v)

    def refresh_candidates(self) -> None:
        self.candidates.clear()
        known = {(s.vid, s.pid) for s in KNOWN_DEVICES}
        try:
            found = [c for c in find_any_cooler_candidates() if (c["vendor_id"], c["product_id"]) not in known]
        except Exception as e:
            self.candidates.addItem(f"USB scan failed: {e}")
            return
        for c in found:
            self.candidates.addItem(f"{c['vendor_id']:04x}:{c['product_id']:04x}   "
                                    f"{c.get('product_string') or '(unnamed)'}   "
                                    f"{c.get('manufacturer_string') or ''}   [{c['_transport']}]")
        if not found:
            self.candidates.addItem("Nothing else that looks like a cooler controller.")

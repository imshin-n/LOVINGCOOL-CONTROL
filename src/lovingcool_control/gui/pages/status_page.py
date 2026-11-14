"""
lovingcool-control :: gui/pages/status_page.py
==============================================
First page: is the service running, is the cooler connected, what's on the
screen, and the brightness. Also lists unrecognised USB devices, to help
add a new model to devices.toml.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core.device import KNOWN_DEVICES, find_any_cooler_candidates, user_config_dir
from lovingcool_control.gui.bridge import MODE_EMBEDDED, MODE_SERVICE, ServiceBridge

log = logging.getLogger(__name__)

MODE_TEXT = {
    MODE_SERVICE: "Background service (lovingcool-controld). The screen keeps running when this window closes.",
    MODE_EMBEDDED: ("Running inside this window: the screen stops when you close it. Install the background "
                    "service (packaging/linux/install_service.sh) to keep it running."),
}


def _panel(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(24, 24, 24, 24)
    lay.setSpacing(12)
    label = QLabel(title)
    label.setObjectName("sectionLabel")
    lay.addWidget(label)
    return frame, lay


class StatusPage(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, bridge: ServiceBridge):
        super().__init__()
        self.bridge = bridge
        self._brightness_dirty = False
        self._build_ui()
        bridge.status_changed.connect(self._on_status)
        bridge.mode_changed.connect(lambda _m: self._on_status(bridge.status))
        self._brightness_timer = QTimer(self)
        self._brightness_timer.setSingleShot(True)
        self._brightness_timer.setInterval(250)
        self._brightness_timer.timeout.connect(self._send_brightness)
        self.refresh_candidates()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)
        title = QLabel("Status")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Your cooler's screen at a glance")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        row = QHBoxLayout()
        row.setSpacing(24)

        dev_panel, dev_lay = _panel("DEVICE")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.lbl_device = QLabel("—")
        self.lbl_model = QLabel("—")
        self.lbl_panel = QLabel("—")
        self.lbl_showing = QLabel("—")
        self.lbl_error = QLabel("")
        for lbl in (self.lbl_device, self.lbl_model, self.lbl_showing, self.lbl_error):
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_error.setStyleSheet("color: #E5484D;")
        form.addRow("Device", self.lbl_device)
        form.addRow("Model", self.lbl_model)
        form.addRow("Panel", self.lbl_panel)
        form.addRow("Showing", self.lbl_showing)
        form.addRow("", self.lbl_error)
        dev_lay.addLayout(form)

        bright_row = QHBoxLayout()
        bright_row.addWidget(QLabel("Brightness"))
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.valueChanged.connect(self._on_brightness_moved)
        self.lbl_brightness = QLabel("—")
        self.lbl_brightness.setMinimumWidth(40)
        bright_row.addWidget(self.brightness, stretch=1)
        bright_row.addWidget(self.lbl_brightness)
        dev_lay.addLayout(bright_row)

        btns = QHBoxLayout()
        self.btn_off = QPushButton("Turn screen off")
        self.btn_off.clicked.connect(lambda: self.bridge.call({"cmd": "off"}, self._after_action))
        self.btn_reconnect = QPushButton("Reconnect")
        self.btn_reconnect.clicked.connect(self._reconnect)
        btns.addWidget(self.btn_off)
        btns.addWidget(self.btn_reconnect)
        btns.addStretch()
        dev_lay.addLayout(btns)
        dev_lay.addStretch()
        row.addWidget(dev_panel, stretch=3)

        svc_panel, svc_lay = _panel("SERVICE")
        self.lbl_mode = QLabel("Starting…")
        self.lbl_mode.setWordWrap(True)
        svc_lay.addWidget(self.lbl_mode)
        hint = QLabel(f"Device list overrides: {user_config_dir() / 'devices.toml'}\n"
                      "Command line: lovingcool-ctl --help")
        hint.setObjectName("pageSubtitle")
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        svc_lay.addWidget(hint)
        svc_lay.addStretch()
        row.addWidget(svc_panel, stretch=2)
        layout.addLayout(row)

        cand_panel, cand_lay = _panel("OTHER USB DEVICES (is your cooler not recognised?)")
        hint2 = QLabel("Devices that might be a cooler controller but aren't in the device list. "
                       "Add its VID:PID to devices.toml (see the README).")
        hint2.setObjectName("pageSubtitle")
        hint2.setWordWrap(True)
        cand_lay.addWidget(hint2)
        self.candidates = QListWidget()
        self.candidates.setMaximumHeight(130)
        cand_lay.addWidget(self.candidates)
        refresh = QPushButton("Rescan USB")
        refresh.clicked.connect(self.refresh_candidates)
        cand_lay.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(cand_panel)
        layout.addStretch()

    # ── Updates ───────────────────────────────────────────────

    def _on_status(self, st: dict) -> None:
        self.lbl_mode.setText(MODE_TEXT.get(self.bridge.mode, "Not running: no service could be reached or started."))
        dev = st.get("device") if st.get("connected") else None
        if dev:
            self.lbl_device.setText(f"{dev['name']}  ({dev['usb_id']})")
            self.lbl_model.setText(f"{dev.get('model') or '?'}, firmware {dev.get('firmware') or '?'}")
            panel = dev.get("panel") or []
            self.lbl_panel.setText(" × ".join(str(n) for n in panel) or "—")
        else:
            self.lbl_device.setText("Not connected. Waiting for the cooler to appear…")
            self.lbl_model.setText("—")
            self.lbl_panel.setText("—")
        showing = st.get("content") or "nothing yet"
        self.lbl_showing.setText(showing + ("   ▶ playing" if st.get("playing") else ""))
        self.lbl_error.setText(st.get("error") or "")
        self.lbl_error.setVisible(bool(st.get("error")))
        for w in (self.brightness, self.btn_off, self.btn_reconnect):
            w.setEnabled(self.bridge.mode in (MODE_SERVICE, MODE_EMBEDDED))
        b = st.get("brightness")
        if b is not None and not self._brightness_dirty and not self.brightness.isSliderDown():
            self.brightness.blockSignals(True)
            self.brightness.setValue(int(b))
            self.brightness.blockSignals(False)
            self.lbl_brightness.setText(f"{int(b)}%")

    def _on_brightness_moved(self, value: int) -> None:
        self.lbl_brightness.setText(f"{value}%")
        self._brightness_dirty = True
        self._brightness_timer.start()   # debounce: send once the slider settles

    def _send_brightness(self) -> None:
        value = self.brightness.value()

        def done(reply: dict) -> None:
            self._brightness_dirty = False
            if reply.get("ok"):
                self.log_message.emit(f"[OK] Brightness {value}%")

        self.bridge.call({"cmd": "brightness", "value": value}, done)

    def _reconnect(self) -> None:
        self.log_message.emit("[INFO] Reconnecting…")
        self.bridge.call({"cmd": "reconnect"}, self._after_action)

    def _after_action(self, reply: dict) -> None:
        if reply.get("ok"):
            self.bridge.refresh()

    def refresh_candidates(self) -> None:
        self.candidates.clear()
        known = {(s.vid, s.pid) for s in KNOWN_DEVICES}
        try:
            found = [c for c in find_any_cooler_candidates() if (c["vendor_id"], c["product_id"]) not in known]
        except Exception as e:
            self.candidates.addItem(f"USB scan failed: {e}")
            return
        for c in found:
            name = c.get("product_string") or "(unnamed)"
            mfr = c.get("manufacturer_string") or ""
            self.candidates.addItem(f"{c['vendor_id']:04x}:{c['product_id']:04x}   {name}   {mfr}   "
                                    f"[{c['_transport']}]")
        if not found:
            self.candidates.addItem("Nothing else that looks like a cooler controller.")

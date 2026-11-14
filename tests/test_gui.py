"""GUI as a client of the service, headless (offscreen Qt) against a fake SC60."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lovingcool_control.core.canvas import Widget  # noqa: E402
from lovingcool_control.core.device import DeviceSpec  # noqa: E402
from lovingcool_control.gui.bridge import MODE_EMBEDDED, MODE_SERVICE, ServiceBridge  # noqa: E402
from lovingcool_control.service.daemon import ServiceRunner  # noqa: E402

from .test_service import FakeDevice  # noqa: E402

SPEC = DeviceSpec(name="SC60", vid=0x33C3, pid=0x7791, transport="serial", lcd_width=480, lcd_height=480,
                  lcd_color_format="JPEG")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(qapp, tmp_path, monkeypatch, make_device):
    """Isolated config dir + QSettings, a fake bus with one SC60, and a runner factory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path / "qsettings"))
    from lovingcool_control.service import daemon as daemon_mod
    monkeypatch.setattr(daemon_mod, "find_serial_port", lambda vid, pid: "/dev/fake")
    runners = []

    def factory(socket_path):
        r = ServiceRunner(socket_path, tmp_path / "state.json", poll=0.05, discover=lambda: [FakeDevice(SPEC)])
        runners.append(r)
        return r

    yield {"sock": tmp_path / "ctl.sock", "factory": factory, "runners": runners, "tmp": tmp_path}
    for r in runners:
        try:
            r.stop()
        except Exception:
            pass


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def make_bridge(env, **kw) -> ServiceBridge:
    bridge = ServiceBridge(socket_path=env["sock"], runner_factory=env["factory"], poll_ms=100, **kw)
    bridge.start()
    return bridge


def test_bridge_embeds_a_service_when_none_runs(env):
    bridge = make_bridge(env)
    try:
        assert bridge.mode == MODE_EMBEDDED
        assert wait_until(lambda: bridge.connected)
        assert bridge.panel_size() == (480, 480)
        assert bridge.status["device"]["model"] == "TXW818-ST7701S-4.0inch"
    finally:
        bridge.shutdown()
    assert not env["sock"].exists()          # built-in service went away with the window


def test_bridge_uses_an_existing_service(env):
    external = env["factory"](env["sock"])
    external.start()
    bridge = make_bridge(env)
    try:
        assert bridge.mode == MODE_SERVICE
        replies = []
        bridge.call({"cmd": "text", "text": "hello"}, replies.append)
        assert wait_until(lambda: replies)
        assert replies[0]["ok"] and replies[0]["applied"]
    finally:
        bridge.shutdown()
    assert env["sock"].exists()              # the external service keeps running


def test_errors_are_logged(env):
    bridge = make_bridge(env)
    logs = []
    bridge.log_message.connect(logs.append)
    try:
        bridge.call({"cmd": "show", "path": "/nope.png"})
        assert wait_until(lambda: any("No such file" in m for m in logs))
    finally:
        bridge.shutdown()


def test_panel_size_remembered_when_disconnected(env):
    bridge = make_bridge(env)
    try:
        assert wait_until(lambda: bridge.connected)
        bridge.status = {"connected": False}
        assert bridge.panel_size() == (480, 480)
    finally:
        bridge.shutdown()


def test_device_view_sends_text_and_off(env):
    from lovingcool_control.gui.app import MainWindow

    bridge = ServiceBridge(socket_path=env["sock"], runner_factory=env["factory"], poll_ms=100)
    w = MainWindow(bridge)
    try:
        assert wait_until(lambda: bridge.connected)
        assert wait_until(lambda: "SC60" in w.status_label.text())
        assert w.devices_tab.tabs.tabText(0) == "SC60"          # one side tab per device
        view = w.device_view
        view.mode.setCurrentIndex(view.mode.findData("text"))
        view.text.setText("Hi there")
        view.font_size.setValue(72)
        view.apply()
        assert wait_until(lambda: bridge.status.get("content") == 'text "Hi there"')
        view.mode.setCurrentIndex(view.mode.findData("off"))
        view.apply()
        assert wait_until(lambda: bridge.status.get("content") == "off")
    finally:
        w.close()


def test_designer_live_mode_runs_and_follows_edits(env):
    from lovingcool_control.gui.app import MainWindow
    from lovingcool_control.gui.pages.designer.page import autosave_path, live_scene_path

    bridge = ServiceBridge(socket_path=env["sock"], runner_factory=env["factory"], poll_ms=100)
    w = MainWindow(bridge)
    try:
        assert wait_until(lambda: bridge.connected)
        d = w.designer
        d._widgets.append(Widget(kind="text", text="one", x=10, y=10))
        d._sync_widgets_list()
        d.play_btn.setChecked(True)
        d._toggle_live(True)
        assert wait_until(lambda: d._is_live() and d.play_btn.text() == "Stop Live")
        assert bridge.status["playing"]

        d._widgets[0].text = "two"           # edit while live: pushed after it settles
        assert wait_until(lambda: '"two"' in live_scene_path().read_text(), timeout=3)
        assert wait_until(lambda: '"two"' in autosave_path().read_text(), timeout=3)

        d._toggle_live(False)
        assert wait_until(lambda: not d._is_live() and d.play_btn.text() == "Start Live")
    finally:
        w.close()


def test_designer_reopens_autosaved_work(env):
    from lovingcool_control.gui.app import MainWindow

    bridge = ServiceBridge(socket_path=env["sock"], runner_factory=env["factory"], poll_ms=100)
    w = MainWindow(bridge)
    w.designer._widgets.append(Widget(kind="text", text="keep me"))
    w.designer._set_orientation("portrait")
    assert wait_until(lambda: [x["text"] for x in (w.designer._last_autosaved or {}).get("widgets", [])]
                      == ["keep me"])
    w.close()

    bridge2 = ServiceBridge(socket_path=env["sock"], runner_factory=env["factory"], poll_ms=100)
    w2 = MainWindow(bridge2)
    try:
        assert [x.text for x in w2.designer._widgets] == ["keep me"]
        assert w2.designer._orientation == "portrait"
        assert w2.designer.orientation.currentData() == "portrait"
    finally:
        w2.close()


def test_scene_mode_and_bottom_bar_use_saved_scenes(env):
    from lovingcool_control.core.canvas import SceneConfig, save_scene
    from lovingcool_control.gui.app import MainWindow
    from lovingcool_control.gui.pages.device_view import scenes_dir

    scenes_dir().mkdir(parents=True)
    save_scene(SceneConfig(widgets=[Widget(kind="text", text="saved")]), scenes_dir() / "mine.json")
    save_scene(SceneConfig(), scenes_dir() / "designer.json")            # working file: not listed

    bridge = ServiceBridge(socket_path=env["sock"], runner_factory=env["factory"], poll_ms=100)
    w = MainWindow(bridge)
    try:
        assert wait_until(lambda: bridge.connected)
        assert [w.scene_combo.itemText(i) for i in range(w.scene_combo.count())] == ["mine"]
        view = w.device_view
        assert [view.scene.itemText(i) for i in range(view.scene.count())] == ["mine"]
        view.mode.setCurrentIndex(view.mode.findData("scene"))
        assert view.preview.pixmap() is not None and not view.preview.pixmap().isNull()
        w._show_scene()
        assert wait_until(lambda: bridge.status.get("kind") == "scene"
                          and bridge.status.get("path", "").endswith("mine.json"))
    finally:
        w.close()


def test_no_device_shows_placeholder_tab(env, monkeypatch):
    from lovingcool_control.gui.app import MainWindow

    def empty_factory(socket_path):
        r = ServiceRunner(socket_path, env["tmp"] / "state.json", poll=0.05, discover=lambda: [])
        env["runners"].append(r)
        return r

    bridge = ServiceBridge(socket_path=env["sock"], runner_factory=empty_factory, poll_ms=100)
    w = MainWindow(bridge)
    try:
        assert wait_until(lambda: "Waiting" in w.status_label.text())
        assert w.devices_tab.tabs.tabText(0) == "No device"
    finally:
        w.close()


def test_theme_switch(qapp):
    from lovingcool_control.gui.theme import apply_theme

    assert apply_theme(qapp, "dark") == "dark"
    assert qapp.palette().window().color().lightness() < 100
    assert apply_theme(qapp, "light") == "light"
    assert qapp.palette().window().color().lightness() > 150

"""The background service: reconnect/restore logic, socket API, CLI, state and scene files."""

from __future__ import annotations

import json
import time

import pytest
from PIL import Image

from lovingcool_control import cli
from lovingcool_control.core.canvas import (
    SceneConfig,
    Widget,
    load_scene,
    save_scene,
    scene_from_dict,
    scene_to_dict,
)
from lovingcool_control.core.device import AIODevice, DeviceSpec
from lovingcool_control.core.protocol import build_frame
from lovingcool_control.service import daemon as daemon_mod
from lovingcool_control.service.client import ServiceUnavailable, request
from lovingcool_control.service.daemon import Display
from lovingcool_control.service.server import Server
from lovingcool_control.service.state import Content, State, load_state, save_state

from .conftest import FakeSerial

INFO = {"width": 480, "height": 480, "model": "TXW818-ST7701S-4.0inch", "version": "3.1"}


class AutoReplySerial(FakeSerial):
    """Answers get_info like the SC60 does."""

    def write(self, data: bytes) -> int:
        if data == build_frame(0x06):
            self.response += build_frame(0x06, json.dumps({"data": INFO}).encode())
        return super().write(data)


class FakeDevice(AIODevice):
    def connect(self) -> None:
        self._dev = AutoReplySerial()
        self._connected = True


@pytest.fixture
def world(monkeypatch, make_device, tmp_path):
    """A Display whose USB bus has one fake SC60 that can be 'unplugged'."""
    make_device()   # applies the fast-sleep patch to device.py
    bus = {"present": True, "devices": []}
    spec = DeviceSpec(name="SC60", vid=0x33C3, pid=0x7791, transport="serial", lcd_width=480,
                      lcd_height=480, lcd_color_format="JPEG")

    def discover():
        if not bus["present"]:
            return []
        dev = FakeDevice(spec)
        bus["devices"].append(dev)
        return [dev]

    monkeypatch.setattr(daemon_mod, "find_serial_port",
                        lambda vid, pid: "/dev/fake" if bus["present"] else None)
    display = Display(tmp_path / "state.json", discover=discover)
    yield display, bus, tmp_path
    display.shutdown()


def writes(bus) -> list[bytes]:
    return bus["devices"][-1]._dev.writes if bus["devices"] else []


def test_connects_and_reports_status(world):
    display, bus, _ = world
    display.tick()
    st = display.status()
    assert st["connected"] and st["device"]["model"] == "TXW818-ST7701S-4.0inch"
    assert st["device"]["panel"] == [480, 480] and st["content"] is None


def test_content_set_while_disconnected_is_applied_on_connect(world):
    display, bus, tmp_path = world
    bus["present"] = False
    display.tick()
    r = display.set_content(Content(kind="text", text="hi"))
    assert r == {"applied": False, "content": 'text "hi"'}
    assert load_state(tmp_path / "state.json").content.text == "hi"

    bus["present"] = True
    display.tick()
    sent = writes(bus)
    assert build_frame(0x11) in sent                     # stream started
    assert any(w[:4] != b"\x55\xaa\x07\x00" and len(w) > 100 for w in sent)   # a JPEG frame went out


def test_unplug_and_replug_restores_content_and_brightness(world):
    display, bus, tmp_path = world
    img = tmp_path / "pic.png"
    Image.new("RGB", (64, 64), (0, 255, 0)).save(img)
    display.tick()
    display.set_brightness(30)
    display.set_content(Content.for_file(str(img)))

    bus["present"] = False
    display.tick()
    assert not display.status()["connected"]

    bus["present"] = True
    display.tick()
    sent = writes(bus)
    assert build_frame(0x03, bytes([30])) in sent
    assert display.status()["content"] == f"image {img}"


def test_restart_restores_from_state_file(world, make_device):
    display, bus, tmp_path = world
    save_state(State(content=Content(kind="text", text="persist"), brightness=55), tmp_path / "state.json")
    fresh = Display(tmp_path / "state.json", discover=display._discover)
    fresh.tick()
    try:
        assert build_frame(0x03, bytes([55])) in writes(bus)
        assert fresh.status()["content"] == 'text "persist"'
    finally:
        fresh.shutdown()


def test_bad_requests(world):
    display, _, tmp_path = world
    assert display.handle({"cmd": "show", "path": str(tmp_path / "missing.png")}) == {
        "ok": False, "error": f"No such file: {tmp_path / 'missing.png'}"}
    assert not display.handle({"cmd": "frobnicate"})["ok"]
    assert not display.handle({"cmd": "brightness", "value": 101})["ok"]
    assert not display.handle({"cmd": "show"})["ok"]                 # missing path
    bad_scene = tmp_path / "scene.json"
    bad_scene.write_text("{not json")
    assert "scene" in display.handle({"cmd": "scene", "path": str(bad_scene)})["error"]


def test_scene_runs_and_off_stops_it(world):
    display, bus, tmp_path = world
    scene = tmp_path / "scene.json"
    save_scene(SceneConfig(widgets=[Widget(kind="stat", metric_key="clock")], fps=20), scene)
    display.tick()
    assert display.handle({"cmd": "scene", "path": str(scene)})["applied"]
    time.sleep(0.3)
    assert display.status()["playing"]
    assert display.handle({"cmd": "off"})["ok"]
    assert not display.status()["playing"]
    assert writes(bus)[-1] == b"\xff\xd9\xff\xd9"                    # stream ended


def test_socket_round_trip(tmp_path):
    sock = tmp_path / "ctl.sock"
    server = Server(sock, lambda req: {"ok": True, "echo": req})
    server.start()
    try:
        assert request({"cmd": "ping"}, sock) == {"ok": True, "echo": {"cmd": "ping"}}
        assert oct(sock.stat().st_mode & 0o777) == "0o600"
        with pytest.raises(RuntimeError, match="already running"):
            Server(sock, lambda req: {})
    finally:
        server.stop()
    assert not sock.exists()
    with pytest.raises(ServiceUnavailable):
        request({"cmd": "ping"}, sock)


def test_stale_socket_is_replaced(tmp_path):
    sock = tmp_path / "ctl.sock"
    sock.write_text("")                     # leftover from a crash: exists but nobody listens
    server = Server(sock, lambda req: {"ok": True})
    server.start()
    try:
        assert request({"cmd": "x"}, sock) == {"ok": True}
    finally:
        server.stop()


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["status"], {"cmd": "status"}),
        (["show", "a.gif", "--once", "--portrait"],
         {"cmd": "show", "path": "a.gif", "loop": False, "rotation": 0, "orientation": "portrait"}),
        (["text", "hi", "--fg", "#f80", "--size", "60"],
         {"cmd": "text", "text": "hi", "fg": [255, 136, 0], "bg": [0, 0, 0], "font_size": 60,
          "orientation": "landscape"}),
        (["brightness", "40"], {"cmd": "brightness", "value": 40}),
        (["off"], {"cmd": "off"}),
    ],
)
def test_cli_requests(argv, expected):
    assert cli.to_request(cli.build_parser().parse_args(argv)) == expected


def test_cli_rejects_bad_values():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["brightness", "150"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["text", "x", "--fg", "zzzzzz"])


def test_cli_status_text():
    out = cli.format_status({"connected": True, "content": "gif /x.gif", "playing": True, "brightness": 70,
                             "device": {"name": "SC60", "usb_id": "33c3:7791", "model": "M", "firmware": "3.1",
                                        "panel": [480, 480]}})
    assert "SC60 (33c3:7791)" in out and "480x480" in out and "[playing]" in out and "70%" in out


def test_scene_dict_round_trip(tmp_path):
    cfg = SceneConfig(orientation="portrait", rotation=12.5, fps=15,
                      widgets=[Widget(kind="text", text="hi", color=(1, 2, 3)), Widget(kind="cpu_graph")])
    assert scene_from_dict(json.loads(json.dumps(scene_to_dict(cfg)))) == cfg
    save_scene(cfg, tmp_path / "s.json")
    assert load_scene(tmp_path / "s.json") == cfg


def test_scene_from_dict_tolerates_old_or_partial_files():
    cfg = scene_from_dict({"widgets": [{"kind": "stat", "color": [300, -5, 10]}], "fps": 999})
    assert cfg.widgets[0].color == (255, 0, 10) and cfg.fps == 60 and cfg.background.kind == "color"
    with pytest.raises(TypeError):
        scene_from_dict({"widgets": [{"kind": "text", "bogus": 1}]})


def test_state_file_round_trip_and_corruption(tmp_path):
    path = tmp_path / "state.json"
    st = State(content=Content(kind="video", path="/v.mp4", loop=False), brightness=20)
    save_state(st, path)
    assert load_state(path) == st
    path.write_text("garbage")
    assert load_state(path) == State()


def test_content_validation():
    with pytest.raises(ValueError):
        Content(kind="image")
    with pytest.raises(ValueError):
        Content(kind="nope")
    assert Content.for_file("/a/b.MP4").kind == "video"
    assert Content.for_file("/a/b.gif").kind == "gif"
    assert Content.for_file("/a/b.png").kind == "image"

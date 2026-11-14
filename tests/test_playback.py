"""LCDController: shared instance, single playback slot, frame pacing, JPEG streaming."""

from __future__ import annotations

import io
import threading
import time

from PIL import Image

from lovingcool_control.core.canvas import BackgroundConfig, SceneConfig, ScenePlayer
from lovingcool_control.core.lcd import LCDController, pace_frames
from lovingcool_control.core.protocol import build_frame


def controller(make_device):
    d, fake = make_device()
    ctl = LCDController.for_device(d)
    ctl._info_loaded = True        # skip get_info; FakeSerial has no reply queued
    return d, fake, ctl


def test_one_controller_per_device(make_device):
    d1, _ = make_device()
    d2, _ = make_device()
    assert LCDController.for_device(d1) is LCDController.for_device(d1)
    assert LCDController.for_device(d1) is not LCDController.for_device(d2)


def test_send_image_commits_once_then_sends_jpeg(make_device):
    _, fake, ctl = controller(make_device)
    ctl.send_image(Image.new("RGB", (100, 50), (200, 10, 10)))
    ctl.send_image(Image.new("RGB", (100, 50), (10, 200, 10)))
    assert fake.writes[0] == build_frame(0x11)
    assert len(fake.writes) == 3
    for jpeg in fake.writes[1:]:
        img = Image.open(io.BytesIO(jpeg))
        assert img.format == "JPEG" and img.size == (480, 480)


def test_new_playback_replaces_old(make_device):
    _, _, ctl = controller(make_device)
    first_running = threading.Event()

    def first(stop):
        first_running.set()
        stop.wait(5)

    stop1 = ctl.start_playback(first, "first")
    assert first_running.wait(1)
    stop2 = ctl.start_playback(lambda stop: stop.wait(5), "second")
    assert stop1.is_set() and not stop2.is_set()
    assert ctl.playing
    ctl.stop_playback()
    assert stop2.is_set() and not ctl.playing


def test_playback_error_is_reported(make_device):
    _, _, ctl = controller(make_device)

    def boom(_stop):
        raise RuntimeError("decoder exploded")

    ctl.start_playback(boom, "boom")
    ctl._play_thread.join(1)
    assert ctl.last_error == "decoder exploded" and not ctl.playing


def test_scene_is_displaced_by_other_playback(make_device):
    _, fake, ctl = controller(make_device)
    player = ScenePlayer(ctl)
    player.start(SceneConfig(background=BackgroundConfig(kind="color"), fps=50))
    deadline = time.monotonic() + 2
    while len(fake.writes) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert player.running and player.last_frame is not None
    ctl.start_playback(lambda stop: stop.wait(5), "gif")
    assert not player.running
    player.stop()                  # must not tear down the playback that replaced it
    assert ctl.playing
    ctl.stop_playback()


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeStop:
    """threading.Event stand-in whose wait() advances the fake clock instead of sleeping."""

    def __init__(self, clock):
        self.clock = clock

    def is_set(self):
        return False

    def wait(self, seconds):
        self.clock.t += seconds
        return False


def test_pace_frames_keeps_schedule_and_skips_when_behind():
    clock = FakeClock()
    shown = []

    def slow_show(img):
        shown.append(img)
        clock.t += 0.25        # each send takes 250 ms but frames are 100 ms apart

    frames = [(i, 100) for i in range(10)]
    pace_frames(frames, FakeStop(clock), slow_show, clock=clock)
    # ~1 s of video at 250 ms per send: every 2nd/3rd frame shown, timing kept
    assert shown[0] == 0 and 3 <= len(shown) <= 5
    assert shown == sorted(shown)


def test_pace_frames_shows_everything_when_fast_enough():
    clock = FakeClock()
    shown = []
    pace_frames([(i, 40) for i in range(25)], FakeStop(clock), shown.append, clock=clock)
    assert shown == list(range(25))
    assert abs(clock.t - 1.0) < 1e-9


def test_keepalive_resends_last_frame_until_stop(make_device):
    d, fake = make_device(keepalive_ms=40)
    ctl = LCDController.for_device(d)
    ctl._info_loaded = True
    ctl.send_image(Image.new("RGB", (10, 10)))
    frame = fake.writes[-1]
    time.sleep(0.3)
    resent = fake.writes[2:]
    assert len(resent) >= 3 and all(w == frame for w in resent)
    ctl.stop()
    count = len(fake.writes)
    time.sleep(0.15)
    assert len(fake.writes) == count            # nothing after stop


def test_no_keepalive_by_default(make_device):
    _, fake, ctl = controller(make_device)
    ctl.send_image(Image.new("RGB", (10, 10)))
    time.sleep(0.15)
    assert len(fake.writes) == 2                 # commit + one frame


def test_command_mid_stream_ends_and_resumes_stream(make_device):
    d, fake = make_device(keepalive_ms=10_000)
    ctl = LCDController.for_device(d)
    ctl._info_loaded = True
    ctl._use_frame_wrapper = True                # firmware > 2.8 behaviour
    ctl.send_image(Image.new("RGB", (10, 10)))
    frame = fake.writes[-1]
    fake.writes.clear()
    ctl.set_brightness(55)
    assert fake.writes == [
        b"\xff\xd9\xff\xd9",                     # end the stream so the command is heard
        build_frame(0x03, bytes([55])),
        build_frame(0x11),                       # start live again
        frame,                                   # and put the picture back
    ]
    ctl.stop()


def test_scene_skips_unchanged_frames(make_device):
    _, fake, ctl = controller(make_device)
    player = ScenePlayer(ctl)
    # Static text only: every frame after the first is identical.
    from lovingcool_control.core.canvas import Widget
    player.start(SceneConfig(widgets=[Widget(kind="text", text="static")], fps=30))
    deadline = time.monotonic() + 2
    while player.frames_skipped < 10 and time.monotonic() < deadline:
        time.sleep(0.02)
    player.stop()
    assert player.frames_sent == 1 and player.frames_skipped >= 10
    jpegs = [w for w in fake.writes if w[:2] == b"\xff\xd8"]
    assert len(jpegs) == 1


def test_still_background_is_scaled_once(monkeypatch):
    from PIL import Image

    from lovingcool_control.core import canvas
    from lovingcool_control.core.canvas import SceneConfig, compose_scene
    from lovingcool_control.core.sensors import CpuHistory

    calls = []
    real = canvas.resize_to_canvas
    monkeypatch.setattr(canvas, "resize_to_canvas", lambda *a: calls.append(1) or real(*a))
    photo = Image.new("RGB", (1280, 1280), (10, 20, 30))
    cfg = SceneConfig()
    first = compose_scene(cfg, 480, 480, photo, CpuHistory(), metrics={})
    second = compose_scene(cfg, 480, 480, photo, CpuHistory(), metrics={})
    assert len(calls) == 1
    assert first.tobytes() == second.tobytes() and first is not second
    compose_scene(cfg, 480, 480, Image.new("RGB", (1280, 1280)), CpuHistory(), metrics={})   # new frame: rescaled
    assert len(calls) == 2

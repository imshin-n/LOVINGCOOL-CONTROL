"""
lovingcool-control :: service/daemon.py
=======================================
lovingcool-controld: the background service that owns the cooler's screen.

- Finds and connects to the device, and keeps retrying while it's absent.
- Shows what was last asked for (image, GIF, video, text, scene) and keeps
  it there: it re-applies it after a reconnect or a restart (state.json).
- Answers JSON requests on a local Unix socket; `lovingcool-ctl` is the
  command-line client, and the GUI will become one too.

It never imports Qt, so it runs without a display: start it from a
systemd --user unit at login, or by hand with `lovingcool-controld`.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from ..core.canvas import ScenePlayer, load_scene
from ..core.device import AIODevice, DeviceError, find_devices, find_serial_port
from ..core.lcd import LCDController, load_image
from .server import Server, default_socket_path
from .state import Content, State, default_state_path, load_state, save_state

log = logging.getLogger("lovingcool-controld")


class RequestError(Exception):
    """A request that can't be done; the message goes back to the client."""


class Display:
    """
    Owns the device connection and what it shows. Every public method is
    thread-safe (one lock), so socket requests and the reconnect monitor
    can't interleave.
    """

    def __init__(self, state_path: Path, discover: Callable[[], list[AIODevice]] = find_devices):
        self._lock = threading.RLock()
        self._discover = discover
        self.state_path = state_path
        self.state: State = load_state(state_path)
        self.device: AIODevice | None = None
        self.lcd: LCDController | None = None
        self._scene: ScenePlayer | None = None
        self.last_error: str | None = None
        self._last_connect_error: str | None = None

    # ── Connection management ─────────────────────────────────

    def tick(self) -> None:
        """One monitor pass: notice a lost device, or try to (re)connect."""
        with self._lock:
            if self.device is not None and not self._device_present():
                log.warning("Lost %s", self.device.spec.name)
                self._drop()
            if self.device is None:
                self._try_connect()

    def _device_present(self) -> bool:
        dev = self.device
        if dev is None or not dev.connected:
            return False
        if dev.spec.transport == "serial":
            # Catches an unplug even while nothing is being written.
            return find_serial_port(dev.spec.vid, dev.spec.pid) is not None
        return True

    def _try_connect(self) -> None:
        for dev in self._discover():
            try:
                dev.connect()
                lcd = LCDController.for_device(dev)
                info = lcd.refresh_info()
            except DeviceError as e:
                msg = f"{dev.spec.name}: {e}"
                if msg != self._last_connect_error:   # don't repeat the same error every tick
                    log.error("Connect failed: %s", msg)
                    self._last_connect_error = msg
                dev.disconnect()
                continue
            self.device, self.lcd = dev, lcd
            self._last_connect_error = None
            log.info("Connected: %s (%s), model %s, firmware %s, panel %dx%d",
                     dev.spec.name, dev.spec.usb_id, info.get("model", "?"), info.get("version", "?"),
                     *lcd.size)
            self._restore()
            return

    def _restore(self) -> None:
        if self.state.brightness is not None:
            try:
                self.lcd.set_brightness(self.state.brightness)
            except DeviceError as e:
                log.error("Could not restore brightness: %s", e)
        if self.state.content is not None:
            try:
                self._apply(self.state.content)
                log.info("Restored: %s", self.state.content.describe())
            except Exception as e:
                self.last_error = f"Could not restore {self.state.content.describe()}: {e}"
                log.error(self.last_error)

    def _drop(self) -> None:
        self._stop_content(end_stream=False)
        if self.device is not None:
            self.device.disconnect()
        self.device = self.lcd = None

    def shutdown(self) -> None:
        with self._lock:
            if self.lcd is not None:
                self._stop_content(end_stream=True)
            if self.device is not None:
                self.device.disconnect()
            self.device = self.lcd = None

    # ── Content ───────────────────────────────────────────────

    def _stop_content(self, end_stream: bool) -> None:
        if self._scene is not None:
            self._scene.stop()
            self._scene = None
        if self.lcd is not None:
            self.lcd.stop_playback()
            if end_stream:
                try:
                    self.lcd.stop()
                except DeviceError:
                    pass

    def _apply(self, c: Content) -> None:
        lcd = self.lcd
        assert lcd is not None
        self._stop_content(end_stream=False)
        self.last_error = None
        if c.kind == "image":
            lcd.send_image(load_image(c.path), c.rotation, c.orientation)
        elif c.kind == "gif":
            lcd.play_gif(c.path, loop=c.loop, rotation=c.rotation, orientation=c.orientation)
        elif c.kind == "video":
            lcd.play_video(c.path, loop=c.loop, rotation=c.rotation, orientation=c.orientation)
        elif c.kind == "text":
            lcd.send_text(c.text, font_size=c.font_size, fg=c.fg, bg=c.bg, orientation=c.orientation)
        elif c.kind == "scene":
            self._scene = ScenePlayer(lcd)
            self._scene.start(load_scene(c.path))
        elif c.kind == "off":
            if lcd.device.spec.keepalive_ms <= 0:
                # Panels that hold the last frame need an explicit black one.
                lcd.send_image(Image.new("RGB", lcd.size))
            self._stop_content(end_stream=True)

    def set_content(self, c: Content) -> dict:
        """Show c now if connected, and remember it either way."""
        if c.path:
            c.path = str(Path(c.path).expanduser().resolve())
            if not Path(c.path).is_file():
                raise RequestError(f"No such file: {c.path}")
        if c.kind == "scene":
            try:
                load_scene(c.path)
            except Exception as e:
                raise RequestError(f"Not a valid scene file: {e}") from e
        with self._lock:
            applied = False
            if self.lcd is not None:
                try:
                    self._apply(c)
                    applied = True
                except DeviceError as e:
                    raise RequestError(f"Device error: {e}") from e
                except Exception as e:
                    raise RequestError(f"Could not show {c.describe()}: {e}") from e
            self.state.content = c
            save_state(self.state, self.state_path)
            return {"applied": applied, "content": c.describe()}

    def set_brightness(self, percent: int) -> dict:
        if not 0 <= percent <= 100:
            raise RequestError("brightness must be 0-100")
        with self._lock:
            applied = False
            if self.lcd is not None:
                try:
                    self.lcd.set_brightness(percent)
                    applied = True
                except DeviceError as e:
                    raise RequestError(f"Device error: {e}") from e
            self.state.brightness = percent
            save_state(self.state, self.state_path)
            return {"applied": applied, "brightness": percent}

    def status(self) -> dict:
        with self._lock:
            dev, lcd = self.device, self.lcd
            errors = [e for e in (self.last_error,
                                  self._scene.last_error if self._scene else None,
                                  lcd.last_error if lcd else None) if e]
            return {
                "connected": dev is not None,
                "device": None if dev is None else {
                    "name": dev.spec.name,
                    "usb_id": dev.spec.usb_id,
                    "model": dev.info.get("model"),
                    "firmware": dev.info.get("version"),
                    "panel": list(lcd.size) if lcd else None,
                },
                "content": self.state.content.describe() if self.state.content else None,
                "playing": bool(lcd and lcd.playing),
                "brightness": self.state.brightness,
                "error": errors[0] if errors else (None if dev else self._last_connect_error),
            }

    def reconnect(self) -> dict:
        with self._lock:
            if self.device is not None:
                self._drop()
            self._try_connect()
            return {"connected": self.device is not None}

    # ── Request dispatch (from the socket server) ─────────────

    def handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        try:
            if cmd == "ping":
                return {"ok": True, "pong": True}
            if cmd == "status":
                return {"ok": True, **self.status()}
            if cmd == "show":
                opts = {k: req[k] for k in ("loop", "rotation", "orientation") if k in req}
                return {"ok": True, **self.set_content(Content.for_file(str(req["path"]), **opts))}
            if cmd == "text":
                opts = {k: req[k] for k in ("fg", "bg", "font_size", "orientation") if k in req}
                return {"ok": True, **self.set_content(Content(kind="text", text=str(req["text"]), **opts))}
            if cmd == "scene":
                return {"ok": True, **self.set_content(Content(kind="scene", path=str(req["path"])))}
            if cmd == "off":
                return {"ok": True, **self.set_content(Content(kind="off"))}
            if cmd == "brightness":
                return {"ok": True, **self.set_brightness(int(req["value"]))}
            if cmd == "reconnect":
                return {"ok": True, **self.reconnect()}
            return {"ok": False, "error": f"unknown command {cmd!r}"}
        except RequestError as e:
            return {"ok": False, "error": str(e)}
        except (KeyError, TypeError, ValueError) as e:
            return {"ok": False, "error": f"bad request: {e}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lovingcool-controld", description="Background service for the cooler's screen.")
    ap.add_argument("--socket", type=Path, default=default_socket_path(), help="control socket path")
    ap.add_argument("--state", type=Path, default=default_state_path(), help="state file path")
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between device checks")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    display = Display(args.state)
    try:
        server = Server(args.socket, display.handle)
    except RuntimeError as e:
        log.error("%s", e)
        return 1

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    server.start()
    log.info("Listening on %s (pid %d); state in %s", args.socket, os.getpid(), args.state)
    try:
        while not stop.is_set():
            try:
                display.tick()
            except Exception:
                log.exception("Monitor error")
            stop.wait(args.poll)
    finally:
        log.info("Shutting down")
        server.stop()
        display.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

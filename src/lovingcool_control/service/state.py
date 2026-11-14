"""
What the screen should show, persisted so the service can restore it after
a restart, a reboot or the cooler being unplugged and plugged back in.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.device import user_config_dir
from ..core.lcd import VIDEO_EXTENSIONS

log = logging.getLogger(__name__)

KINDS = ("image", "gif", "video", "text", "scene", "off")


@dataclass
class Content:
    kind: str                                   # one of KINDS
    path: str | None = None                     # image / gif / video / scene file
    text: str | None = None
    fg: tuple[int, int, int] = (255, 255, 255)  # text colours
    bg: tuple[int, int, int] = (0, 0, 0)
    font_size: int = 40
    loop: bool = True                           # gif / video
    rotation: int = 0                           # fine rotation, degrees
    orientation: str = "landscape"              # "landscape" | "portrait"

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown content kind {self.kind!r} (expected one of {', '.join(KINDS)})")
        if self.kind in ("image", "gif", "video", "scene") and not self.path:
            raise ValueError(f"{self.kind} needs a file path")
        if self.kind == "text" and not self.text:
            raise ValueError("text needs some text")
        if self.orientation not in ("landscape", "portrait"):
            raise ValueError(f"orientation must be landscape or portrait, not {self.orientation!r}")
        self.fg = tuple(self.fg)
        self.bg = tuple(self.bg)

    @classmethod
    def for_file(cls, path: str, **kw) -> Content:
        """Pick image / gif / video from the file extension."""
        lower = path.lower()
        kind = "gif" if lower.endswith(".gif") else "video" if lower.endswith(VIDEO_EXTENSIONS) else "image"
        return cls(kind=kind, path=path, **kw)

    def describe(self) -> str:
        if self.kind == "text":
            return f'text "{self.text}"'
        if self.kind == "off":
            return "off"
        return f"{self.kind} {self.path}"


@dataclass
class State:
    content: Content | None = None
    brightness: int | None = None
    extra: dict = field(default_factory=dict)   # room for later settings without a format change

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "content": asdict(self.content) if self.content else None,
            "brightness": self.brightness,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> State:
        content = data.get("content")
        brightness = data.get("brightness")
        return cls(
            content=Content(**content) if content else None,
            brightness=int(brightness) if brightness is not None else None,
            extra=dict(data.get("extra") or {}),
        )


def default_state_path() -> Path:
    return user_config_dir() / "state.json"


def load_state(path: Path) -> State:
    try:
        return State.from_dict(json.loads(path.read_text("utf8")))
    except FileNotFoundError:
        return State()
    except (OSError, ValueError, TypeError) as e:
        log.error("Ignoring unreadable state file %s: %s", path, e)
        return State()


def save_state(state: State, path: Path) -> None:
    """Write atomically, so a crash mid-write can't leave a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2), "utf8")
    os.replace(tmp, path)

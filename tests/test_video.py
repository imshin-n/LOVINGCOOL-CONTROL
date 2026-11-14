"""Lazy video decoding (core/lcd.iter_video_frames) on a small generated clip."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from lovingcool_control.core.lcd import iter_video_frames, load_video_thumbnail  # noqa: E402


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (1920, 1080))
    assert writer.isOpened()
    for i in range(5):
        writer.write(np.full((1080, 1920, 3), i * 40, dtype=np.uint8))
    writer.release()
    return path


def test_frames_are_downscaled_while_decoding(clip):
    frames = list(iter_video_frames(clip, max_side=480))
    assert len(frames) == 5
    assert all(img.size == (480, 270) for img, _ in frames)
    assert all(ms == 40 for _, ms in frames)


def test_loop_reopens_the_file(clip):
    frames = list(itertools.islice(iter_video_frames(clip, max_side=64, loop=True), 12))
    assert len(frames) == 12


def test_thumbnail_is_first_frame(clip):
    assert load_video_thumbnail(clip).size == (1920, 1080)


def test_missing_file_raises(tmp_path):
    with pytest.raises(RuntimeError):
        next(iter_video_frames(tmp_path / "nope.mp4"))


def test_background_preview_decodes_one_frame_and_caches(clip):
    from lovingcool_control.core.canvas import BackgroundConfig, background_preview_frame

    cfg = BackgroundConfig(kind="video", path=str(clip))
    first = background_preview_frame(cfg)
    assert first.size == (1024, 576)
    assert background_preview_frame(cfg) is first

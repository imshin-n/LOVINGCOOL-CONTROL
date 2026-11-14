"""
lovingcool-control :: gui/pages/designer/page.py
================================================
Screen Designer: put widgets (stats, text, a CPU graph) over a background
(colour, image, GIF or video), drag them into place, and run it live.

- Start Live writes scenes/designer-live.json and asks the service to run
  it, so it survives closing the window (with the background service).
  While live, edits are pushed once they've settled for PUSH_SETTLE_S.
- Work in progress is autosaved to scenes/designer.json and reopened.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core import sensors
from lovingcool_control.core.canvas import (
    BackgroundConfig,
    SceneConfig,
    background_preview_frame,
    load_scene,
    logical_canvas_size,
    render_frame,
    save_scene,
    scene_metric_keys,
    scene_to_dict,
)
from lovingcool_control.core.device import user_config_dir
from lovingcool_control.gui.bridge import ServiceBridge
from lovingcool_control.gui.pages.device_view import ColorButton

from .preview import ScenePreview
from .widget_dialog import WidgetDialog, describe_widget

log = logging.getLogger(__name__)

PUSH_SETTLE_S = 0.4
BACKGROUNDS = [("color", "Colour"), ("image", "Image"), ("gif", "GIF"), ("video", "Video")]
FITS = [("cover", "Fill (crop)"), ("contain", "Fit (letterbox)"), ("stretch", "Stretch"), ("center", "Original size")]
FILE_FILTERS = {
    "image": "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
    "gif": "GIF (*.gif)",
    "video": "Video (*.mp4 *.avi *.mov *.mkv *.webm)",
}


def scenes_dir() -> Path:
    return user_config_dir() / "scenes"


def live_scene_path() -> Path:
    return scenes_dir() / "designer-live.json"


def autosave_path() -> Path:
    return scenes_dir() / "designer.json"


def _rgb(c: QColor) -> tuple[int, int, int]:
    return c.red(), c.green(), c.blue()


class ScreenDesignerPage(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, bridge: ServiceBridge):
        super().__init__()
        self.bridge = bridge
        self._widgets = []                     # bound to the preview: dragging edits these in place
        self._bg_path: str | None = None
        self._orientation = "landscape"
        self._history = sensors.CpuHistory()
        self._preview_key = None
        self._last_autosaved: dict | None = None
        self._last_pushed: dict | None = None
        self._pending: dict | None = None
        self._pending_since = 0.0
        self._push_in_flight = False

        self._build()
        self.preview.bind_widgets(self._widgets)
        self._restore_autosave()

        self._timer = QTimer(self, interval=200)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        bridge.status_changed.connect(self._on_status)

    # ── Layout ────────────────────────────────────────────────

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left = QVBoxLayout()
        canvas_box = QGroupBox("Preview")
        cl = QVBoxLayout(canvas_box)
        self.preview = ScenePreview()
        self.preview.widget_moved.connect(self._on_dragged)
        self.preview.selection_changed.connect(self._on_canvas_selection)
        self.preview.request_delete.connect(self._delete_widget_at)
        cl.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.canvas_label = QLabel()
        self.canvas_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.canvas_label)
        tip = QLabel("Click a widget to select it, drag to move, Delete to remove.")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setWordWrap(True)
        cl.addWidget(tip)
        live_row = QHBoxLayout()
        self.play_btn = QPushButton("Start Live")
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(self._toggle_live)
        self.fps = QSpinBox()
        self.fps.setRange(1, 30)
        self.fps.setValue(10)
        self.fps.setSuffix(" fps")
        live_row.addWidget(self.play_btn, stretch=1)
        live_row.addWidget(self.fps)
        cl.addLayout(live_row)
        left.addWidget(canvas_box)
        left.addStretch()
        root.addLayout(left)

        right = QVBoxLayout()

        bg_box = QGroupBox("Background")
        bf = QFormLayout(bg_box)
        self.bg_kind = QComboBox()
        for key, label in BACKGROUNDS:
            self.bg_kind.addItem(label, key)
        self.bg_kind.currentIndexChanged.connect(self._on_bg_kind)
        bf.addRow("Type:", self.bg_kind)
        file_row = QHBoxLayout()
        self.bg_file = QLineEdit()
        self.bg_file.setReadOnly(True)
        self.bg_file.setPlaceholderText("No file selected")
        self.bg_browse = QPushButton("Browse…")
        self.bg_browse.clicked.connect(self._choose_bg_file)
        file_row.addWidget(self.bg_file, stretch=1)
        file_row.addWidget(self.bg_browse)
        self.file_label = QLabel("File:")
        bf.addRow(self.file_label, file_row)
        self.fit = QComboBox()
        for key, label in FITS:
            self.fit.addItem(label, key)
        self.fit.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        self.fit_label = QLabel("Fit:")
        bf.addRow(self.fit_label, self.fit)
        colors = QHBoxLayout()
        self.bg_color = ColorButton(QColor(14, 17, 20), "Background colour")
        self.fill_color = ColorButton(QColor(14, 17, 20), "Fill colour")
        for b in (self.bg_color, self.fill_color):
            b.changed.connect(lambda _c: self._refresh_preview())
        colors.addWidget(QLabel("Colour"))
        colors.addWidget(self.bg_color)
        colors.addSpacing(12)
        colors.addWidget(QLabel("Fill behind transparency"))
        colors.addWidget(self.fill_color)
        colors.addStretch()
        bf.addRow("Colours:", colors)
        right.addWidget(bg_box)

        layout_box = QGroupBox("Layout")
        lf = QFormLayout(layout_box)
        self.orientation = QComboBox()
        self.orientation.addItem("Landscape", "landscape")
        self.orientation.addItem("Portrait", "portrait")
        self.orientation.currentIndexChanged.connect(lambda _i: self._set_orientation(self.orientation.currentData()))
        lf.addRow("Orientation:", self.orientation)
        rot = QHBoxLayout()
        self.rot_slider = QSlider(Qt.Orientation.Horizontal)
        self.rot_slider.setRange(0, 3600)
        self.rot_spin = QDoubleSpinBox()
        self.rot_spin.setRange(0.0, 360.0)
        self.rot_spin.setDecimals(1)
        self.rot_spin.setSuffix("°")
        self.rot_slider.valueChanged.connect(lambda v: self._sync_rotation(v / 10.0))
        self.rot_spin.valueChanged.connect(self._sync_rotation)
        rot.addWidget(self.rot_slider, stretch=1)
        rot.addWidget(self.rot_spin)
        lf.addRow("Rotation:", rot)
        right.addWidget(layout_box)

        widgets_box = QGroupBox("Widgets")
        wl = QVBoxLayout(widgets_box)
        self.widget_list = QListWidget()
        self.widget_list.setMinimumHeight(90)
        self.widget_list.currentRowChanged.connect(self._on_list_selection)
        self.widget_list.itemDoubleClicked.connect(lambda _item: self._edit_widget())
        wl.addWidget(self.widget_list)
        buttons = QHBoxLayout()
        for text, slot in (("Add…", self._add_widget), ("Edit…", self._edit_widget),
                           ("Duplicate", self._duplicate_widget), ("Remove", self._remove_widget)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        wl.addLayout(buttons)
        self.snap = QCheckBox("Snap to a 10 px grid while dragging")
        self.snap.toggled.connect(lambda on: setattr(self.preview, "snap", 10 if on else 0))
        wl.addWidget(self.snap)
        right.addWidget(widgets_box, stretch=1)

        files = QHBoxLayout()
        files.addStretch()
        open_btn = QPushButton("Open Scene…")
        open_btn.clicked.connect(self._open_scene)
        save_btn = QPushButton("Save Scene As…")
        save_btn.clicked.connect(self._save_scene_as)
        files.addWidget(open_btn)
        files.addWidget(save_btn)
        right.addLayout(files)
        root.addLayout(right, stretch=1)

        self._on_bg_kind()

    # ── Background, layout ────────────────────────────────────

    def _on_bg_kind(self) -> None:
        has_file = self.bg_kind.currentData() != "color"
        for w in (self.file_label, self.bg_file, self.bg_browse, self.fit_label, self.fit):
            w.setVisible(has_file)
        self._refresh_preview()

    def _choose_bg_file(self) -> None:
        start = str(Path(self._bg_path).parent) if self._bg_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose a background", start,
                                              FILE_FILTERS.get(self.bg_kind.currentData(), "All files (*)"))
        if path:
            self._set_bg_path(path)
            self._refresh_preview()

    def _set_bg_path(self, path: str | None) -> None:
        self._bg_path = path
        self.bg_file.setText(Path(path).name if path else "")
        self.bg_file.setToolTip(path or "")

    def _sync_rotation(self, degrees: float) -> None:
        for w, value in ((self.rot_spin, degrees), (self.rot_slider, int(round(degrees * 10)))):
            w.blockSignals(True)
            w.setValue(value)
            w.blockSignals(False)
        self._refresh_preview()

    def _set_orientation(self, orientation: str) -> None:
        """Switch orientation, rescaling widget positions so they stay on the canvas."""
        orientation = "portrait" if orientation == "portrait" else "landscape"
        if self.orientation.currentData() != orientation:
            self.orientation.blockSignals(True)
            self.orientation.setCurrentIndex(self.orientation.findData(orientation))
            self.orientation.blockSignals(False)
        if orientation == self._orientation:
            return
        old_w, old_h = self._canvas_size()
        self._orientation = orientation
        new_w, new_h = self._canvas_size()
        for w in self._widgets:
            w.x = min(new_w, max(0, round(w.x * new_w / old_w)))
            w.y = min(new_h, max(0, round(w.y * new_h / old_h)))
        self._sync_widgets_list()
        self._refresh_preview()

    def _panel_size(self) -> tuple[int, int]:
        return self.bridge.panel_size()

    def _canvas_size(self) -> tuple[int, int]:
        return logical_canvas_size(*self._panel_size(), self._orientation)

    # ── Widget list ───────────────────────────────────────────

    def _sync_widgets_list(self) -> None:
        row = self.widget_list.currentRow()
        self.widget_list.blockSignals(True)
        self.widget_list.clear()
        self.widget_list.addItems([describe_widget(w) for w in self._widgets])
        self.widget_list.setCurrentRow(min(row, len(self._widgets) - 1))
        self.widget_list.blockSignals(False)

    def _current_row(self) -> int | None:
        row = self.widget_list.currentRow()
        if 0 <= row < len(self._widgets):
            return row
        self.log_message.emit("[ERROR] Select a widget first.")
        return None

    def _add_widget(self) -> None:
        dialog = WidgetDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._widgets.append(dialog.to_widget())
            self._after_list_change(len(self._widgets) - 1)

    def _edit_widget(self) -> None:
        row = self._current_row()
        if row is None:
            return
        dialog = WidgetDialog(self, initial=self._widgets[row])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._widgets[row] = dialog.to_widget()
            self._after_list_change(row)

    def _duplicate_widget(self) -> None:
        row = self._current_row()
        if row is not None:
            src = self._widgets[row]
            self._widgets.append(dataclasses.replace(src, x=src.x + 15, y=src.y + 15))
            self._after_list_change(len(self._widgets) - 1)

    def _remove_widget(self) -> None:
        row = self._current_row()
        if row is not None:
            self._delete_widget_at(row)

    def _delete_widget_at(self, row: int) -> None:
        if 0 <= row < len(self._widgets):
            del self._widgets[row]
            self._after_list_change(min(row, len(self._widgets) - 1))

    def _after_list_change(self, select: int) -> None:
        self._sync_widgets_list()
        self.widget_list.setCurrentRow(select)
        self._refresh_preview()

    def _on_list_selection(self, row: int) -> None:
        self.preview.select(row if row >= 0 else None, emit=False)
        self._refresh_preview()

    def _on_canvas_selection(self, index) -> None:
        self.widget_list.blockSignals(True)
        self.widget_list.setCurrentRow(-1 if index is None else index)
        self.widget_list.blockSignals(False)
        self._refresh_preview()

    def _on_dragged(self) -> None:
        index = self.preview.selected_index
        if index is not None and index < self.widget_list.count():
            self.widget_list.item(index).setText(describe_widget(self._widgets[index]))
        self._refresh_preview()

    # ── Scene <-> editor ──────────────────────────────────────

    def _build_scene_config(self) -> SceneConfig:
        return SceneConfig(
            background=BackgroundConfig(kind=self.bg_kind.currentData(), path=self._bg_path,
                                        color=_rgb(self.bg_color.color), fit_mode=self.fit.currentData()),
            transparent_fill=_rgb(self.fill_color.color),
            orientation=self._orientation,
            rotation=self.rot_spin.value(),
            widgets=list(self._widgets),
            fps=self.fps.value(),
        )

    def _apply_scene(self, cfg: SceneConfig) -> None:
        """Load a scene into the editor."""
        self._widgets[:] = cfg.widgets        # same list object: the preview holds it
        self._orientation = cfg.orientation
        self.orientation.blockSignals(True)
        self.orientation.setCurrentIndex(self.orientation.findData(cfg.orientation))
        self.orientation.blockSignals(False)
        self.bg_kind.setCurrentIndex(max(0, self.bg_kind.findData(cfg.background.kind)))
        self.fit.setCurrentIndex(max(0, self.fit.findData(cfg.background.fit_mode)))
        self._set_bg_path(cfg.background.path)
        self.bg_color.set_color(QColor(*cfg.background.color))
        self.fill_color.set_color(QColor(*cfg.transparent_fill))
        self._sync_rotation(float(cfg.rotation))
        self.fps.setValue(int(cfg.fps))
        self._sync_widgets_list()
        self._refresh_preview()

    # ── Preview, live sync, autosave ──────────────────────────

    def _refresh_preview(self, data: dict | None = None) -> None:
        cfg = self._build_scene_config()
        data = data if data is not None else scene_to_dict(cfg)
        w, h = self._canvas_size()
        where = "connected panel" if self.bridge.connected else "last known panel"
        self.canvas_label.setText(f"{w} × {h} px, {self._orientation} ({where})")
        try:
            metrics = sensors.read_all(scene_metric_keys(cfg))
            # Redraw only if something visible changed (stats change at most once a second).
            key = (repr(data), tuple(sorted((k, m.display) for k, m in metrics.items())),
                   self.preview.selected_index, self._panel_size())
            if key == self._preview_key:
                return
            frame = render_frame(cfg, *self._panel_size(), background_preview_frame(cfg.background),
                                 self._history, highlight_index=self.preview.selected_index, metrics=metrics)
            self.preview.set_image(frame, metrics=metrics)
            self._preview_key = key
        except Exception as e:
            self._preview_key = None
            self.log_message.emit(f"[ERROR] Preview failed: {e}")

    def _tick(self) -> None:
        data = scene_to_dict(self._build_scene_config())
        if self.isVisible():
            self._refresh_preview(data)
        if data != self._last_autosaved:
            self._autosave(data)
        if self._is_live() and data != self._last_pushed and not self._push_in_flight:
            now = time.monotonic()
            if data != self._pending:
                self._pending, self._pending_since = data, now
            elif now - self._pending_since >= PUSH_SETTLE_S:
                self._push_live()

    def _is_live(self) -> bool:
        st = self.bridge.status
        return st.get("kind") == "scene" and st.get("path") == str(live_scene_path())

    def _on_status(self, _status: dict) -> None:
        live = self._is_live()
        self.play_btn.setChecked(live)
        self.play_btn.setText("Stop Live" if live else "Start Live")

    def _push_live(self, announce: bool = False) -> None:
        cfg = self._build_scene_config()
        try:
            scenes_dir().mkdir(parents=True, exist_ok=True)
            save_scene(cfg, live_scene_path())
        except OSError as e:
            self.log_message.emit(f"[ERROR] Could not write the live scene: {e}")
            return
        sent = scene_to_dict(cfg)
        self._push_in_flight = True

        def done(reply: dict) -> None:
            self._push_in_flight = False
            if not reply.get("ok"):
                if announce:
                    self.play_btn.setChecked(False)
                return
            self._last_pushed = sent
            if announce:
                self.log_message.emit("[OK] Screen Designer is live" if reply.get("applied")
                                      else "[INFO] Live scene saved; it starts when the cooler connects")
            self.bridge.refresh()

        self.bridge.call({"cmd": "scene", "path": str(live_scene_path())}, done)

    def _toggle_live(self, checked: bool) -> None:
        if checked:
            self._push_live(announce=True)
            return

        def done(reply: dict) -> None:
            if reply.get("ok"):
                self.log_message.emit("[OK] Live scene stopped; screen off")
                self.bridge.refresh()

        self.bridge.call({"cmd": "off"}, done)

    def _autosave(self, data: dict) -> None:
        self._last_autosaved = data   # even on failure, so a read-only disk isn't retried 5×/s
        try:
            scenes_dir().mkdir(parents=True, exist_ok=True)
            save_scene(self._build_scene_config(), autosave_path())
        except OSError as e:
            log.warning("Autosave failed: %s", e)

    def _restore_autosave(self) -> None:
        if autosave_path().is_file():
            try:
                self._apply_scene(load_scene(autosave_path()))
            except Exception as e:
                log.warning("Ignoring unreadable autosave %s: %s", autosave_path(), e)
        self._last_autosaved = scene_to_dict(self._build_scene_config())

    # ── Scene files ───────────────────────────────────────────

    def _open_scene(self) -> None:
        start = str(scenes_dir()) if scenes_dir().is_dir() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open scene", start, "Scene (*.json)")
        if not path:
            return
        try:
            self._apply_scene(load_scene(path))
            self.log_message.emit(f"[OK] Opened scene {Path(path).stem}")
        except Exception as e:
            self.log_message.emit(f"[ERROR] Could not open scene: {e}")
            QMessageBox.warning(self, "Couldn't open scene", str(e))

    def _save_scene_as(self) -> None:
        scenes_dir().mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save scene", str(scenes_dir() / "scene.json"), "Scene (*.json)")
        if not path:
            return
        try:
            save_scene(self._build_scene_config(), path)
            self.log_message.emit(f"[OK] Saved scene {Path(path).stem}")
        except OSError as e:
            self.log_message.emit(f"[ERROR] Could not save scene: {e}")

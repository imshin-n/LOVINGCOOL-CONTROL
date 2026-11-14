"""
lovingcool-control :: gui/pages/designer/page.py
================================================
Screen Designer: layer a background (colour, image, GIF or video) with live
widgets (hardware stats, clock, text, a CPU graph), drag them into place,
rotate the whole thing, save/load it as a scene file, and run it live.

"Start Live" saves the scene to scenes/designer-live.json and asks the
service to run it, so it keeps running after this window closes (with the
background service). While live, edits are pushed to the screen as you
make them. Your work in progress is autosaved to scenes/designer.json and
reopened next time.
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
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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

from .preview import ScenePreview
from .widget_dialog import AddWidgetDialog, describe_widget

log = logging.getLogger(__name__)

FIT_MODES = [("cover", "Cover (crop to fill)"), ("contain", "Contain (letterbox)"),
             ("stretch", "Stretch (distort)"), ("center", "Center (no scale)")]
BG_KINDS = [("color", "Solid Color"), ("image", "Image"), ("gif", "GIF"), ("video", "Video")]
PUSH_SETTLE_S = 0.4   # while live, push an edit once it has been stable this long


def scenes_dir() -> Path:
    return user_config_dir() / "scenes"


def live_scene_path() -> Path:
    return scenes_dir() / "designer-live.json"


def autosave_path() -> Path:
    return scenes_dir() / "designer.json"


class ScreenDesignerPage(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, bridge: ServiceBridge):
        super().__init__()
        self.bridge = bridge
        self._background_path: str | None = None
        self._background_kind = "color"
        self._fit_mode = "cover"
        self._orientation = "landscape"
        self._bg_color = QColor(14, 17, 20)
        self._fill_color = QColor(14, 17, 20)
        self._widgets = []
        self._preview_history = sensors.CpuHistory()
        # Live sync / autosave bookkeeping (compare scene dicts, not widgets)
        self._last_pushed: dict | None = None
        self._pending: dict | None = None
        self._pending_since = 0.0
        self._last_autosaved: dict | None = None
        self._push_in_flight = False
        self._preview_key = None

        self._build_ui()
        self.preview.bind_widgets(self._widgets)
        self._restore_autosave()

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._tick)
        self._preview_timer.start()
        bridge.status_changed.connect(self._on_status)

    # ── UI construction ──────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        content = QHBoxLayout()
        content.setSpacing(12)

        # Left: preview
        preview_panel = QFrame()
        preview_panel.setObjectName("panel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview = ScenePreview()
        self.preview.widget_moved.connect(self._on_widget_dragged)
        self.preview.selection_changed.connect(self._on_preview_selection_changed)
        self.preview.request_delete.connect(self._delete_widget_at)
        preview_layout.addWidget(self.preview)

        self.canvas_size_label = QLabel("Canvas: —")
        self.canvas_size_label.setObjectName("pageSubtitle")
        self.canvas_size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.canvas_size_label)

        hint = QLabel("Click a widget to select, drag to move, Del to remove")
        hint.setObjectName("pageSubtitle")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(hint)

        play_row = QHBoxLayout()
        self.play_btn = QPushButton("Start Live")
        self.play_btn.setObjectName("primaryButton")
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(self._toggle_live)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 30)
        self.fps_spin.setValue(10)
        self.fps_spin.setSuffix(" fps")
        play_row.addWidget(self.play_btn)
        play_row.addWidget(self.fps_spin)
        preview_layout.addLayout(play_row)

        content.addWidget(preview_panel, stretch=0)

        # Right: controls
        controls_panel = QFrame()
        controls_panel.setObjectName("panel")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(12, 12, 12, 12)
        controls_layout.setSpacing(14)

        bg_label = QLabel("Background")
        bg_label.setObjectName("sectionLabel")
        controls_layout.addWidget(bg_label)

        bg_type_row = QHBoxLayout()
        self.bg_type_combo = QComboBox()
        for key, label in BG_KINDS:
            self.bg_type_combo.addItem(label, key)
        self.bg_type_combo.currentIndexChanged.connect(self._on_bg_type_changed)
        self.fit_mode_combo = QComboBox()
        for key, label in FIT_MODES:
            self.fit_mode_combo.addItem(label, key)
        self.fit_mode_combo.currentIndexChanged.connect(self._on_fit_mode_changed)
        bg_type_row.addWidget(self.bg_type_combo)
        bg_type_row.addWidget(self.fit_mode_combo)
        controls_layout.addLayout(bg_type_row)

        bg_file_row = QHBoxLayout()
        self.bg_file_field = QLineEdit()
        self.bg_file_field.setReadOnly(True)
        self.bg_file_field.setPlaceholderText("No file selected...")
        self.bg_browse_btn = QPushButton("Browse...")
        self.bg_browse_btn.clicked.connect(self._browse_background)
        bg_file_row.addWidget(self.bg_file_field)
        bg_file_row.addWidget(self.bg_browse_btn)
        controls_layout.addLayout(bg_file_row)

        color_row = QHBoxLayout()
        self.bg_color_btn = QPushButton()
        self.bg_color_btn.clicked.connect(self._pick_bg_color)
        self.fill_color_btn = QPushButton()
        self.fill_color_btn.clicked.connect(self._pick_fill_color)
        color_row.addWidget(QLabel("Solid / fill color:"))
        color_row.addWidget(self.bg_color_btn)
        color_row.addWidget(QLabel("Transparent-bg fill:"))
        color_row.addWidget(self.fill_color_btn)
        controls_layout.addLayout(color_row)
        self._update_color_buttons()

        orient_label = QLabel("Orientation")
        orient_label.setObjectName("sectionLabel")
        controls_layout.addWidget(orient_label)
        orient_row = QHBoxLayout()
        self.btn_landscape = QPushButton("⬛ Landscape")
        self.btn_portrait = QPushButton("▮ Portrait")
        for b in (self.btn_landscape, self.btn_portrait):
            b.setCheckable(True)
        self.btn_landscape.clicked.connect(lambda: self._set_orientation("landscape"))
        self.btn_portrait.clicked.connect(lambda: self._set_orientation("portrait"))
        orient_row.addWidget(self.btn_landscape)
        orient_row.addWidget(self.btn_portrait)
        controls_layout.addLayout(orient_row)
        orient_hint = QLabel(
            "Swaps the working canvas aspect (e.g. 480x320 <-> 320x480); the panel's "
            "fixed native buffer is rotated to fit at send time. Widgets rescale to stay on-canvas."
        )
        orient_hint.setObjectName("pageSubtitle")
        orient_hint.setWordWrap(True)
        controls_layout.addWidget(orient_hint)

        rot_label = QLabel("Fine rotation (on top of orientation; 180° flips it)")
        rot_label.setObjectName("sectionLabel")
        controls_layout.addWidget(rot_label)
        rot_row = QHBoxLayout()
        self.rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_slider.setRange(0, 3600)   # tenths of a degree
        self.rotation_slider.valueChanged.connect(self._on_rotation_slider)
        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(0.0, 360.0)
        self.rotation_spin.setSuffix("°")
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.valueChanged.connect(self._on_rotation_spin)
        rot_row.addWidget(self.rotation_slider, stretch=1)
        rot_row.addWidget(self.rotation_spin)
        controls_layout.addLayout(rot_row)

        widgets_label = QLabel("Widgets (hardware stats, text, CPU graph)")
        widgets_label.setObjectName("sectionLabel")
        controls_layout.addWidget(widgets_label)
        self.widgets_list = QListWidget()
        self.widgets_list.setMaximumHeight(120)
        self.widgets_list.currentRowChanged.connect(self._on_list_selection_changed)
        controls_layout.addWidget(self.widgets_list)

        widget_btn_row = QHBoxLayout()
        for text, slot, name in (("Add...", self._add_widget, ""), ("Edit...", self._edit_widget, ""),
                                 ("Duplicate", self._duplicate_widget, ""),
                                 ("Remove", self._remove_widget, "dangerButton")):
            btn = QPushButton(text)
            if name:
                btn.setObjectName(name)
            btn.clicked.connect(slot)
            widget_btn_row.addWidget(btn)
        controls_layout.addLayout(widget_btn_row)

        self.snap_check = QCheckBox("Snap to 10px grid while dragging")
        self.snap_check.toggled.connect(lambda on: setattr(self.preview, "snap", 10 if on else 0))
        controls_layout.addWidget(self.snap_check)

        preset_label = QLabel("Scene file")
        preset_label.setObjectName("sectionLabel")
        controls_layout.addWidget(preset_label)
        preset_row = QHBoxLayout()
        save_preset_btn = QPushButton("Save As...")
        save_preset_btn.clicked.connect(self._save_preset)
        load_preset_btn = QPushButton("Open...")
        load_preset_btn.clicked.connect(self._load_preset)
        preset_row.addWidget(save_preset_btn)
        preset_row.addWidget(load_preset_btn)
        controls_layout.addLayout(preset_row)

        controls_layout.addStretch()
        content.addWidget(controls_panel, stretch=1)
        layout.addLayout(content)

        self._style_orientation_buttons()
        self._on_bg_type_changed()
        self._plain_look()

    def _plain_look(self) -> None:
        """Native-looking widgets: framed panels, bold section headings, small hints."""
        for frame in self.findChildren(QFrame):
            if frame.objectName() == "panel":
                frame.setFrameShape(QFrame.Shape.StyledPanel)
        for label in self.findChildren(QLabel):
            name = label.objectName()
            if name == "sectionLabel":
                font = label.font()
                font.setBold(True)
                label.setFont(font)
            elif name == "pageSubtitle":
                font = label.font()
                font.setPointSizeF(font.pointSizeF() * 0.9)
                label.setFont(font)

    # ── State helpers ────────────────────────────────────────

    def _update_color_buttons(self):
        self.bg_color_btn.setStyleSheet(f"background-color: {self._bg_color.name()};")
        self.fill_color_btn.setStyleSheet(f"background-color: {self._fill_color.name()};")

    def _on_bg_type_changed(self):
        self._background_kind = self.bg_type_combo.currentData() or "color"
        needs_file = self._background_kind != "color"
        for w in (self.bg_file_field, self.bg_browse_btn, self.fit_mode_combo):
            w.setVisible(needs_file)
        self._refresh_preview()

    def _on_fit_mode_changed(self):
        self._fit_mode = self.fit_mode_combo.currentData() or "cover"
        self._refresh_preview()

    def _browse_background(self):
        filters = {
            "image": "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
            "gif": "GIF (*.gif)",
            "video": "Video (*.mp4 *.avi *.mov *.mkv *.webm)",
        }.get(self._background_kind, "All files (*)")
        start = str(Path(self._background_path).parent) if self._background_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Select background", start, filters)
        if path:
            self._background_path = path
            self.bg_file_field.setText(Path(path).name)
            self._refresh_preview()

    def _pick_bg_color(self):
        color = QColorDialog.getColor(self._bg_color, self, "Solid Background Color")
        if color.isValid():
            self._bg_color = color
            self._update_color_buttons()
            self._refresh_preview()

    def _pick_fill_color(self):
        color = QColorDialog.getColor(self._fill_color, self, "Transparent-Background Fill Color")
        if color.isValid():
            self._fill_color = color
            self._update_color_buttons()
            self._refresh_preview()

    def _on_rotation_slider(self, value: int):
        self.rotation_spin.blockSignals(True)
        self.rotation_spin.setValue(value / 10.0)
        self.rotation_spin.blockSignals(False)
        self._refresh_preview()

    def _on_rotation_spin(self, value: float):
        self.rotation_slider.blockSignals(True)
        self.rotation_slider.setValue(int(value * 10))
        self.rotation_slider.blockSignals(False)
        self._refresh_preview()

    # ── Widget list ──────────────────────────────────────────

    def _sync_widgets_list(self):
        self.widgets_list.blockSignals(True)
        self.widgets_list.clear()
        for w in self._widgets:
            self.widgets_list.addItem(QListWidgetItem(describe_widget(w)))
        self.widgets_list.blockSignals(False)

    def _add_widget(self):
        dialog = AddWidgetDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._widgets.append(dialog.to_widget())
            self._sync_widgets_list()
            self.widgets_list.setCurrentRow(len(self._widgets) - 1)
            self._refresh_preview()

    def _selected_row(self) -> int | None:
        row = self.widgets_list.currentRow()
        if row < 0:
            self.log_message.emit("[ERROR] Select a widget first.")
            return None
        return row

    def _edit_widget(self):
        row = self._selected_row()
        if row is None:
            return
        dialog = AddWidgetDialog(self, initial=self._widgets[row])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._widgets[row] = dialog.to_widget()
            self._sync_widgets_list()
            self.widgets_list.setCurrentRow(row)
            self._refresh_preview()

    def _duplicate_widget(self):
        row = self._selected_row()
        if row is None:
            return
        src = self._widgets[row]
        self._widgets.append(dataclasses.replace(src, x=src.x + 15, y=src.y + 15))
        self._sync_widgets_list()
        self.widgets_list.setCurrentRow(len(self._widgets) - 1)
        self._refresh_preview()

    def _remove_widget(self):
        row = self._selected_row()
        if row is not None:
            self._delete_widget_at(row)

    def _delete_widget_at(self, row: int):
        if 0 <= row < len(self._widgets):
            del self._widgets[row]
            self._sync_widgets_list()
            self._refresh_preview()

    def _on_list_selection_changed(self, row: int):
        self.preview.select(row if row >= 0 else None, emit=False)
        self._refresh_preview()

    def _on_preview_selection_changed(self, idx):
        self.widgets_list.blockSignals(True)
        self.widgets_list.setCurrentRow(idx if idx is not None else -1)
        self.widgets_list.blockSignals(False)
        self._refresh_preview()

    def _on_widget_dragged(self):
        row = self.preview.selected_index
        if row is not None and 0 <= row < self.widgets_list.count():
            self.widgets_list.item(row).setText(describe_widget(self._widgets[row]))
        self._refresh_preview()

    # ── Scene config ─────────────────────────────────────────

    def _build_scene_config(self) -> SceneConfig:
        return SceneConfig(
            background=BackgroundConfig(
                kind=self._background_kind,
                path=self._background_path,
                color=(self._bg_color.red(), self._bg_color.green(), self._bg_color.blue()),
                fit_mode=self._fit_mode,
            ),
            transparent_fill=(self._fill_color.red(), self._fill_color.green(), self._fill_color.blue()),
            orientation=self._orientation,
            rotation=self.rotation_spin.value(),
            widgets=list(self._widgets),
            fps=self.fps_spin.value(),
        )

    def _apply_scene(self, cfg: SceneConfig) -> None:
        """Load a scene into the editor (from a file or the autosave)."""
        self._background_kind = cfg.background.kind
        self._background_path = cfg.background.path
        self._fit_mode = cfg.background.fit_mode
        self._bg_color = QColor(*cfg.background.color)
        self._fill_color = QColor(*cfg.transparent_fill)
        self._widgets[:] = cfg.widgets          # keep the list object bound to the preview
        self._orientation = cfg.orientation
        self._style_orientation_buttons()

        idx = self.bg_type_combo.findData(self._background_kind)
        self.bg_type_combo.setCurrentIndex(max(0, idx))
        fit_index = self.fit_mode_combo.findData(self._fit_mode)
        if fit_index >= 0:
            self.fit_mode_combo.setCurrentIndex(fit_index)
        self.bg_file_field.setText(Path(self._background_path).name if self._background_path else "")
        self._update_color_buttons()
        self.rotation_spin.setValue(float(cfg.rotation))
        self.fps_spin.setValue(int(cfg.fps))
        self._sync_widgets_list()
        self._refresh_preview()

    def _device_native_size(self) -> tuple[int, int]:
        return self.bridge.panel_size()

    def _canvas_size(self) -> tuple[int, int]:
        """The logical (oriented) canvas widgets are positioned on."""
        return logical_canvas_size(*self._device_native_size(), self._orientation)

    def _style_orientation_buttons(self) -> None:
        for btn, key in ((self.btn_landscape, "landscape"), (self.btn_portrait, "portrait")):
            btn.setChecked(self._orientation == key)
            btn.setObjectName("primaryButton" if self._orientation == key else "")
            btn.setStyleSheet("")   # re-polish after an objectName change

    def _set_orientation(self, orientation: str):
        if orientation == self._orientation:
            self._style_orientation_buttons()
            return
        old_w, old_h = self._canvas_size()
        self._orientation = orientation
        self._style_orientation_buttons()
        new_w, new_h = self._canvas_size()
        if old_w > 0 and old_h > 0:   # rescale positions so nothing ends up off-canvas
            sx, sy = new_w / old_w, new_h / old_h
            for w in self._widgets:
                w.x = max(0, min(new_w, int(w.x * sx)))
                w.y = max(0, min(new_h, int(w.y * sy)))
            self._sync_widgets_list()
        self._refresh_preview()

    # ── Preview, live sync and autosave ──────────────────────

    def _refresh_preview(self, data: dict | None = None):
        cfg = self._build_scene_config()
        data = data if data is not None else scene_to_dict(cfg)
        w, h = self._canvas_size()
        source = "from the connected panel" if self.bridge.connected else "last known panel size"
        self.canvas_size_label.setText(f"Canvas: {w} x {h} ({self._orientation}, {source})")
        try:
            metrics = sensors.read_all(scene_metric_keys(cfg))   # once, shared with hit-testing
            # Redraw only when something visible changed: the scene, a stat's text
            # (stats refresh once a second), the selection or the panel size.
            key = (repr(data), tuple(sorted((k, m.display) for k, m in metrics.items())),
                   self.preview.selected_index, self._device_native_size())
            if key == self._preview_key:
                return
            bg = background_preview_frame(cfg.background)
            frame = render_frame(cfg, *self._device_native_size(), bg, self._preview_history,
                                 highlight_index=self.preview.selected_index, for_device=False,
                                 metrics=metrics)
            self.preview.set_image(frame, metrics=metrics)
            self._preview_key = key
        except Exception as e:
            self._preview_key = None
            self.log_message.emit(f"[ERROR] Preview failed: {e}")

    def _tick(self):
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

    def _on_status(self, _st: dict) -> None:
        live = self._is_live()
        self.play_btn.setChecked(live)
        self.play_btn.setText("Stop Live" if live else "Start Live")

    def _push_live(self, announce: bool = False) -> None:
        cfg = self._build_scene_config()
        try:
            live_scene_path().parent.mkdir(parents=True, exist_ok=True)
            save_scene(cfg, live_scene_path())
        except OSError as e:
            self.log_message.emit(f"[ERROR] Could not write the live scene: {e}")
            return
        pushed = scene_to_dict(cfg)
        self._push_in_flight = True

        def done(reply: dict) -> None:
            self._push_in_flight = False
            if reply.get("ok"):
                self._last_pushed = pushed
                if announce:
                    self.log_message.emit("[OK] Screen Designer is live" if reply.get("applied") else
                                          "[INFO] Live scene saved; it will start when the cooler connects")
                self.bridge.refresh()
            elif announce:
                self.play_btn.setChecked(False)

        self.bridge.call({"cmd": "scene", "path": str(live_scene_path())}, done)

    def _toggle_live(self, checked: bool):
        if checked:
            self._push_live(announce=True)
        else:
            def done(reply: dict) -> None:
                if reply.get("ok"):
                    self.log_message.emit("[OK] Live scene stopped; screen off")
                    self.bridge.refresh()
            self.bridge.call({"cmd": "off"}, done)

    def _autosave(self, data: dict) -> None:
        try:
            autosave_path().parent.mkdir(parents=True, exist_ok=True)
            save_scene(self._build_scene_config(), autosave_path())
            self._last_autosaved = data
        except OSError as e:
            log.warning("Autosave failed: %s", e)
            self._last_autosaved = data   # don't retry every tick

    def _restore_autosave(self) -> None:
        if autosave_path().is_file():
            try:
                self._apply_scene(load_scene(autosave_path()))
            except Exception as e:
                log.warning("Ignoring unreadable autosave %s: %s", autosave_path(), e)
        self._last_autosaved = scene_to_dict(self._build_scene_config())

    # ── Scene files ──────────────────────────────────────────

    def _save_preset(self):
        scenes_dir().mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save Scene", str(scenes_dir() / "scene.json"),
                                              "lovingcool-control scene (*.json)")
        if not path:
            return
        try:
            save_scene(self._build_scene_config(), path)
            self.log_message.emit(f"[OK] Saved scene: {path}")
        except Exception as e:
            self.log_message.emit(f"[ERROR] Could not save scene: {e}")

    def _load_preset(self):
        start = str(scenes_dir()) if scenes_dir().is_dir() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open Scene", start, "lovingcool-control scene (*.json)")
        if not path:
            return
        try:
            self._apply_scene(load_scene(path))
            self.log_message.emit(f"[OK] Opened scene: {Path(path).name}")
        except Exception as e:
            self.log_message.emit(f"[ERROR] Could not open scene: {e}")
            QMessageBox.warning(self, "Open Scene Failed", str(e))

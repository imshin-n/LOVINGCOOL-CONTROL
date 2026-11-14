"""
lovingcool-control :: gui/pages/screen_designer.py
============================================
Customizable "screen" builder: layer a background (solid color, image,
GIF, or video — with transparent-background flattening and Cover/Contain/
Stretch/Center fit modes) with live hardware-stat widgets (CPU/GPU %,
temperatures, RAM, disk, network, battery, uptime, clock), a CPU history
sparkline, drag-to-position widgets directly in the preview, rotate the
whole composition by any angle, save/load the scene as a JSON preset, and
stream it live to the LCD. Canvas size auto-follows the connected
device's actual resolution.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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
    ScenePlayer,
    Widget,
    load_background_frames,
    render_frame,
    widget_bbox,
)
from lovingcool_control.core.lcd import LCDController

log = logging.getLogger(__name__)

DEFAULT_CANVAS = (480, 320)   # fallback aspect before a device reports its own size
FIT_MODES = [("cover", "Cover (crop to fill)"), ("contain", "Contain (letterbox)"),
             ("stretch", "Stretch (distort)"), ("center", "Center (no scale)")]


class ScenePreview(QLabel):
    """
    Aspect-correct, interactive preview: click a widget to select it,
    drag to reposition, Delete/Backspace to remove the selection.
    """

    widget_moved = pyqtSignal()
    selection_changed = pyqtSignal(object)   # int index or None
    request_delete = pyqtSignal(int)

    def __init__(self, max_width: int = 380, max_height: int = 280):
        super().__init__()
        self._max_w = max_width
        self._max_h = max_height
        self.setFixedSize(max_width, max_height)
        self.setStyleSheet(
            "background-color: #0E1014; border: 2px solid #2C323D; "
            "border-radius: 12px; color: #8B93A1; font-size: 12px;"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("No preview")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self._widgets: list[Widget] = []
        self._canvas_size = DEFAULT_CANVAS
        self._display_size = (0, 0)
        self._display_offset = (0, 0)
        self._last_metrics: dict = {}
        self._dragging_index: int | None = None
        self._drag_offset = (0, 0)
        self._selected_index: int | None = None
        self.snap = 0   # grid snap size in canvas px, 0 = off

    def bind_widgets(self, widgets: list[Widget]) -> None:
        """Bind the live widget list by reference — drag edits it in place."""
        self._widgets = widgets

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    def select(self, idx: int | None, emit: bool = True) -> None:
        self._selected_index = idx
        if emit:
            self.selection_changed.emit(idx)

    def set_image(self, img, metrics: dict | None = None) -> None:
        if metrics is not None:
            self._last_metrics = metrics
        img = img.convert("RGB")
        w, h = img.size
        self._canvas_size = (w, h)
        scale = min(self._max_w / w, self._max_h / h)
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        self._display_size = new_size
        self._display_offset = ((self._max_w - new_size[0]) // 2, (self._max_h - new_size[1]) // 2)
        disp = img.resize(new_size)
        data = disp.tobytes("raw", "RGB")
        qimg = QImage(data, disp.width, disp.height, disp.width * 3, QImage.Format.Format_RGB888)
        self.setPixmap(QPixmap.fromImage(qimg))

    def _map_to_canvas(self, pos: QPoint) -> tuple[int, int] | None:
        dw, dh = self._display_size
        if dw <= 0 or dh <= 0:
            return None
        ox, oy = self._display_offset
        lx, ly = pos.x() - ox, pos.y() - oy
        if lx < 0 or ly < 0 or lx >= dw or ly >= dh:
            return None
        cw, ch = self._canvas_size
        return int(lx * cw / dw), int(ly * ch / dh)

    def _hit_test(self, cx: int, cy: int) -> int | None:
        for i in reversed(range(len(self._widgets))):
            x0, y0, x1, y1 = widget_bbox(self._widgets[i], self._last_metrics)
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                return i
        return None

    def mousePressEvent(self, event):
        self.setFocus()
        pos = self._map_to_canvas(event.position().toPoint())
        if pos is None:
            self.select(None)
            return
        cx, cy = pos
        idx = self._hit_test(cx, cy)
        self.select(idx)
        if idx is not None:
            w = self._widgets[idx]
            self._dragging_index = idx
            self._drag_offset = (cx - w.x, cy - w.y)

    def mouseMoveEvent(self, event):
        if self._dragging_index is None:
            return
        pos = self._map_to_canvas(event.position().toPoint())
        if pos is None:
            return
        cx, cy = pos
        cw, ch = self._canvas_size
        new_x = cx - self._drag_offset[0]
        new_y = cy - self._drag_offset[1]
        if self.snap:
            new_x = round(new_x / self.snap) * self.snap
            new_y = round(new_y / self.snap) * self.snap
        w = self._widgets[self._dragging_index]
        w.x = max(0, min(cw, new_x))
        w.y = max(0, min(ch, new_y))
        self.widget_moved.emit()

    def mouseReleaseEvent(self, event):
        self._dragging_index = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._selected_index is not None:
            idx = self._selected_index
            self.select(None)
            self.request_delete.emit(idx)
        else:
            super().keyPressEvent(event)


WIDGET_KIND_LABELS = {"text": "Custom Text", "stat": "Hardware Stat", "cpu_graph": "CPU Graph (sparkline)"}


class AddWidgetDialog(QDialog):
    """Form for configuring one overlay widget. Position can be left at default
    and then dragged into place afterward in the preview."""

    def __init__(self, parent=None, initial: Widget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Widget")
        self.setMinimumWidth(320)

        form = QFormLayout(self)

        self.kind_combo = QComboBox()
        for key, label in WIDGET_KIND_LABELS.items():
            self.kind_combo.addItem(label, key)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("Type", self.kind_combo)

        self.text_field = QLineEdit()
        self.text_field.setPlaceholderText("Text to display")
        form.addRow("Text", self.text_field)

        self.metric_combo = QComboBox()
        for key, label in sensors.METRIC_CHOICES:
            self.metric_combo.addItem(label, key)
        form.addRow("Metric", self.metric_combo)

        self.show_label_check = QCheckBox("Show label (e.g. \"CPU: 42%\" vs \"42%\")")
        self.show_label_check.setChecked(True)
        form.addRow("", self.show_label_check)

        self.x_spin = QSpinBox()
        self.x_spin.setRange(0, 4000)
        self.x_spin.setValue(10)
        self.y_spin = QSpinBox()
        self.y_spin.setRange(0, 4000)
        self.y_spin.setValue(10)
        pos_row = QHBoxLayout()
        pos_row.addWidget(self.x_spin)
        pos_row.addWidget(QLabel("x"))
        pos_row.addWidget(self.y_spin)
        form.addRow("Position (X, Y)", pos_row)
        hint = QLabel("Tip: drag the widget directly in the preview afterward.")
        hint.setObjectName("pageSubtitle")
        form.addRow("", hint)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 200)
        self.font_size_spin.setValue(28)
        form.addRow("Font size", self.font_size_spin)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(20, 2000)
        self.width_spin.setValue(140)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(20, 2000)
        self.height_spin.setValue(48)
        size_row = QHBoxLayout()
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("x"))
        size_row.addWidget(self.height_spin)
        form.addRow("Graph size (W, H)", size_row)

        self._color = QColor(255, 255, 255)
        self.color_btn = QPushButton()
        self._update_color_btn()
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow("Color", self.color_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        if initial is not None:
            self._load_from(initial)
        self._on_kind_changed()

    def _load_from(self, w: Widget):
        index = self.kind_combo.findData(w.kind)
        if index >= 0:
            self.kind_combo.setCurrentIndex(index)
        self.text_field.setText(w.text)
        metric_index = self.metric_combo.findData(w.metric_key)
        if metric_index >= 0:
            self.metric_combo.setCurrentIndex(metric_index)
        self.show_label_check.setChecked(w.show_label)
        self.x_spin.setValue(w.x)
        self.y_spin.setValue(w.y)
        self.font_size_spin.setValue(w.font_size)
        self.width_spin.setValue(w.width)
        self.height_spin.setValue(w.height)
        self._color = QColor(*w.color)
        self._update_color_btn()

    def _update_color_btn(self):
        self.color_btn.setStyleSheet(f"background-color: {self._color.name()};")
        self.color_btn.setText(self._color.name())

    def _pick_color(self):
        color = QColorDialog.getColor(self._color, self, "Widget Color")
        if color.isValid():
            self._color = color
            self._update_color_btn()

    def _on_kind_changed(self):
        kind = self.kind_combo.currentData()
        self.text_field.setVisible(kind == "text")
        self.metric_combo.setVisible(kind == "stat")
        self.show_label_check.setVisible(kind == "stat")
        self.font_size_spin.setVisible(kind in ("text", "stat"))
        is_graph = kind == "cpu_graph"
        self.width_spin.setVisible(is_graph)
        self.height_spin.setVisible(is_graph)

    def to_widget(self) -> Widget:
        kind = self.kind_combo.currentData()
        color = (self._color.red(), self._color.green(), self._color.blue())
        return Widget(
            kind=kind,
            x=self.x_spin.value(),
            y=self.y_spin.value(),
            font_size=self.font_size_spin.value(),
            color=color,
            text=self.text_field.text(),
            metric_key=self.metric_combo.currentData() or "cpu_percent",
            show_label=self.show_label_check.isChecked(),
            width=self.width_spin.value(),
            height=self.height_spin.value(),
        )


def _describe_widget(w: Widget) -> str:
    if w.kind == "text":
        return f"Text @ ({w.x},{w.y}): \"{w.text}\""
    if w.kind == "stat":
        label = dict(sensors.METRIC_CHOICES).get(w.metric_key, w.metric_key)
        return f"Stat @ ({w.x},{w.y}): {label}"
    if w.kind == "cpu_graph":
        return f"CPU Graph @ ({w.x},{w.y}) {w.width}x{w.height}"
    return w.kind


class ScreenDesignerPage(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, get_device_callback):
        super().__init__()
        self._get_device = get_device_callback
        self._lcd_controller: LCDController | None = None
        self._scene_player: ScenePlayer | None = None
        self._background_path: str | None = None
        self._background_kind = "color"
        self._fit_mode = "cover"
        self._orientation = "landscape"
        self._bg_color = QColor(14, 17, 20)
        self._fill_color = QColor(14, 17, 20)
        self._widgets: list[Widget] = []
        self._preview_history = sensors.CpuHistory()
        self._last_canvas_size: tuple[int, int] | None = None

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._tick_preview)

        self._build_ui()
        self.preview.bind_widgets(self._widgets)
        self._preview_timer.start()

    # ── UI construction ──────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        title = QLabel("Screen Designer")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Build a custom live screen: backgrounds, hardware stats, rotation, and more")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        content = QHBoxLayout()
        content.setSpacing(24)

        # Left: preview
        preview_panel = QFrame()
        preview_panel.setObjectName("panel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(24, 24, 24, 24)
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
        controls_layout.setContentsMargins(24, 24, 24, 24)
        controls_layout.setSpacing(14)

        # Background
        bg_label = QLabel("BACKGROUND")
        bg_label.setObjectName("sectionLabel")
        controls_layout.addWidget(bg_label)

        bg_type_row = QHBoxLayout()
        self.bg_type_combo = QComboBox()
        self.bg_type_combo.addItems(["Solid Color", "Image", "GIF", "Video"])
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
        bg_browse_btn = QPushButton("Browse...")
        bg_browse_btn.clicked.connect(self._browse_background)
        bg_file_row.addWidget(self.bg_file_field)
        bg_file_row.addWidget(bg_browse_btn)
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

        # Orientation
        orient_label = QLabel("ORIENTATION")
        orient_label.setObjectName("sectionLabel")
        controls_layout.addWidget(orient_label)

        orient_row = QHBoxLayout()
        self.btn_landscape = QPushButton("⬛ Landscape")
        self.btn_portrait = QPushButton("▮ Portrait")
        for b in (self.btn_landscape, self.btn_portrait):
            b.setCheckable(True)
        self.btn_landscape.setChecked(True)
        self.btn_landscape.setObjectName("primaryButton")
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

        # Rotation
        rot_label = QLabel("FINE ROTATION (on top of orientation — try 180° to flip)")
        rot_label.setObjectName("sectionLabel")
        controls_layout.addWidget(rot_label)

        rot_row = QHBoxLayout()
        self.rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_slider.setRange(0, 3600)   # tenths of a degree -> smooth slider
        self.rotation_slider.valueChanged.connect(self._on_rotation_slider)
        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(0.0, 360.0)
        self.rotation_spin.setSuffix("°")
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.valueChanged.connect(self._on_rotation_spin)
        rot_row.addWidget(self.rotation_slider, stretch=1)
        rot_row.addWidget(self.rotation_spin)
        controls_layout.addLayout(rot_row)

        # Widgets
        widgets_label = QLabel("WIDGETS (hardware stats, text, CPU graph)")
        widgets_label.setObjectName("sectionLabel")
        controls_layout.addWidget(widgets_label)

        self.widgets_list = QListWidget()
        self.widgets_list.setMaximumHeight(120)
        self.widgets_list.currentRowChanged.connect(self._on_list_selection_changed)
        controls_layout.addWidget(self.widgets_list)

        widget_btn_row = QHBoxLayout()
        add_widget_btn = QPushButton("Add...")
        add_widget_btn.clicked.connect(self._add_widget)
        edit_widget_btn = QPushButton("Edit...")
        edit_widget_btn.clicked.connect(self._edit_widget)
        dup_widget_btn = QPushButton("Duplicate")
        dup_widget_btn.clicked.connect(self._duplicate_widget)
        remove_widget_btn = QPushButton("Remove")
        remove_widget_btn.setObjectName("dangerButton")
        remove_widget_btn.clicked.connect(self._remove_widget)
        widget_btn_row.addWidget(add_widget_btn)
        widget_btn_row.addWidget(edit_widget_btn)
        widget_btn_row.addWidget(dup_widget_btn)
        widget_btn_row.addWidget(remove_widget_btn)
        controls_layout.addLayout(widget_btn_row)

        self.snap_check = QCheckBox("Snap to 10px grid while dragging")
        self.snap_check.toggled.connect(self._on_snap_toggled)
        controls_layout.addWidget(self.snap_check)

        # Presets
        preset_label = QLabel("PRESET")
        preset_label.setObjectName("sectionLabel")
        controls_layout.addWidget(preset_label)
        preset_row = QHBoxLayout()
        save_preset_btn = QPushButton("Save Preset...")
        save_preset_btn.clicked.connect(self._save_preset)
        load_preset_btn = QPushButton("Load Preset...")
        load_preset_btn.clicked.connect(self._load_preset)
        preset_row.addWidget(save_preset_btn)
        preset_row.addWidget(load_preset_btn)
        controls_layout.addLayout(preset_row)

        controls_layout.addStretch()
        content.addWidget(controls_panel, stretch=1)
        layout.addLayout(content)

        self._on_bg_type_changed()

    # ── State helpers ────────────────────────────────────────

    def _update_color_buttons(self):
        self.bg_color_btn.setStyleSheet(f"background-color: {self._bg_color.name()};")
        self.fill_color_btn.setStyleSheet(f"background-color: {self._fill_color.name()};")

    def _on_bg_type_changed(self):
        kind_map = {"Solid Color": "color", "Image": "image", "GIF": "gif", "Video": "video"}
        self._background_kind = kind_map[self.bg_type_combo.currentText()]
        needs_file = self._background_kind != "color"
        self.bg_file_field.setVisible(needs_file)
        self.fit_mode_combo.setVisible(needs_file)
        self._refresh_static_preview()

    def _on_fit_mode_changed(self):
        self._fit_mode = self.fit_mode_combo.currentData() or "cover"
        self._refresh_static_preview()

    def _on_snap_toggled(self, checked: bool):
        self.preview.snap = 10 if checked else 0

    def _browse_background(self):
        filters = {
            "image": "Images (*.png *.jpg *.jpeg *.bmp)",
            "gif": "GIF (*.gif)",
            "video": "Video (*.mp4 *.avi *.mov *.mkv *.webm)",
        }.get(self._background_kind, "All files (*)")
        path, _ = QFileDialog.getOpenFileName(self, "Select background", str(Path.home()), filters)
        if path:
            self._background_path = path
            self.bg_file_field.setText(Path(path).name)
            self._refresh_static_preview()

    def _pick_bg_color(self):
        color = QColorDialog.getColor(self._bg_color, self, "Solid Background Color")
        if color.isValid():
            self._bg_color = color
            self._update_color_buttons()
            self._refresh_static_preview()

    def _pick_fill_color(self):
        color = QColorDialog.getColor(self._fill_color, self, "Transparent-Background Fill Color")
        if color.isValid():
            self._fill_color = color
            self._update_color_buttons()
            self._refresh_static_preview()

    def _on_rotation_slider(self, value: int):
        self.rotation_spin.blockSignals(True)
        self.rotation_spin.setValue(value / 10.0)
        self.rotation_spin.blockSignals(False)
        self._refresh_static_preview()

    def _on_rotation_spin(self, value: float):
        self.rotation_slider.blockSignals(True)
        self.rotation_slider.setValue(int(value * 10))
        self.rotation_slider.blockSignals(False)
        self._refresh_static_preview()

    # ── Widget list management ───────────────────────────────

    def _sync_widgets_list(self):
        self.widgets_list.blockSignals(True)
        self.widgets_list.clear()
        for w in self._widgets:
            self.widgets_list.addItem(QListWidgetItem(_describe_widget(w)))
        self.widgets_list.blockSignals(False)

    def _add_widget(self):
        dialog = AddWidgetDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            widget = dialog.to_widget()
            self._widgets.append(widget)
            self._sync_widgets_list()
            self.widgets_list.setCurrentRow(len(self._widgets) - 1)
            self._refresh_static_preview()

    def _edit_widget(self):
        row = self.widgets_list.currentRow()
        if row < 0:
            self.log_message.emit("[ERROR] Select a widget first.")
            return
        dialog = AddWidgetDialog(self, initial=self._widgets[row])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._widgets[row] = dialog.to_widget()
            self._sync_widgets_list()
            self.widgets_list.setCurrentRow(row)
            self._refresh_static_preview()

    def _duplicate_widget(self):
        row = self.widgets_list.currentRow()
        if row < 0:
            self.log_message.emit("[ERROR] Select a widget first.")
            return
        clone = dataclasses.replace(self._widgets[row], x=self._widgets[row].x + 15, y=self._widgets[row].y + 15)
        self._widgets.append(clone)
        self._sync_widgets_list()
        self.widgets_list.setCurrentRow(len(self._widgets) - 1)
        self._refresh_static_preview()

    def _remove_widget(self):
        row = self.widgets_list.currentRow()
        if row < 0:
            self.log_message.emit("[ERROR] Select a widget first.")
            return
        self._delete_widget_at(row)

    def _delete_widget_at(self, row: int):
        if 0 <= row < len(self._widgets):
            del self._widgets[row]
            self._sync_widgets_list()
            self._refresh_static_preview()

    def _on_list_selection_changed(self, row: int):
        self.preview.select(row if row >= 0 else None, emit=False)
        self._refresh_static_preview()

    def _on_preview_selection_changed(self, idx):
        self.widgets_list.blockSignals(True)
        self.widgets_list.setCurrentRow(idx if idx is not None else -1)
        self.widgets_list.blockSignals(False)
        self._refresh_static_preview()

    def _on_widget_dragged(self):
        row = self.preview.selected_index
        if row is not None and 0 <= row < self.widgets_list.count():
            self.widgets_list.item(row).setText(_describe_widget(self._widgets[row]))
        self._refresh_static_preview()

    # ── Scene config / rendering ──────────────────────────────

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
            widgets=self._widgets,
            fps=self.fps_spin.value(),
        )

    def _device_native_size(self) -> tuple[int, int]:
        """The LCD panel's fixed physical raster (unaffected by orientation)."""
        device = self._get_device()
        if device and device.connected:
            return device.spec.lcd_width, device.spec.lcd_height
        return DEFAULT_CANVAS

    def _canvas_size(self) -> tuple[int, int]:
        """The current LOGICAL/oriented canvas — what widgets are positioned against."""
        from lovingcool_control.core.canvas import logical_canvas_size
        w, h = self._device_native_size()
        return logical_canvas_size(w, h, self._orientation)

    def _update_canvas_size_label(self, size: tuple[int, int]):
        device = self._get_device()
        source = "auto-detected from connected device" if (device and device.connected) else "default — connect device for actual size"
        self.canvas_size_label.setText(f"Canvas: {size[0]} x {size[1]} ({self._orientation}, {source})")

    def _set_orientation(self, orientation: str):
        if orientation == self._orientation:
            self.btn_landscape.setChecked(orientation == "landscape")
            self.btn_portrait.setChecked(orientation == "portrait")
            return
        old_w, old_h = self._canvas_size()
        self._orientation = orientation
        self.btn_landscape.setChecked(orientation == "landscape")
        self.btn_portrait.setChecked(orientation == "portrait")
        self.btn_landscape.setObjectName("primaryButton" if orientation == "landscape" else "")
        self.btn_portrait.setObjectName("primaryButton" if orientation == "portrait" else "")
        self.btn_landscape.setStyleSheet("")
        self.btn_portrait.setStyleSheet("")
        new_w, new_h = self._canvas_size()

        # Rescale widget positions proportionally so nothing ends up off-canvas.
        if old_w > 0 and old_h > 0:
            sx, sy = new_w / old_w, new_h / old_h
            for w in self._widgets:
                w.x = max(0, min(new_w, int(w.x * sx)))
                w.y = max(0, min(new_h, int(w.y * sy)))
            self._sync_widgets_list()

        self._refresh_static_preview()

    def _refresh_static_preview(self):
        """Re-render one frame for the preview when not live (cheap, no device I/O)."""
        if self._scene_player and self._scene_player.running:
            return   # live loop is already feeding the preview via _tick_preview
        cfg = self._build_scene_config()
        self._update_canvas_size_label(self._canvas_size())
        native_width, native_height = self._device_native_size()
        try:
            frames = load_background_frames(cfg.background)
            bg = frames[0][0] if frames else None
            # for_device=False -> logical/oriented frame, matching what drag-editing expects
            frame = render_frame(cfg, native_width, native_height, bg, self._preview_history,
                                  highlight_index=self.preview.selected_index, for_device=False)
            self.preview.set_image(frame, metrics=sensors.read_all())
        except Exception as e:
            self.log_message.emit(f"[ERROR] Preview failed: {e}")

    def _tick_preview(self):
        if self._scene_player and self._scene_player.running:
            if self._scene_player.last_frame is not None:
                self.preview.set_image(self._scene_player.last_frame, metrics=sensors.read_all())
            if self._scene_player.last_error:
                self.log_message.emit(f"[ERROR] {self._scene_player.last_error}")
                self._scene_player.last_error = None
        else:
            self._refresh_static_preview()

    # ── Device / playback ────────────────────────────────────

    def _ensure_controller(self) -> LCDController | None:
        device = self._get_device()
        if not device or not device.connected:
            self.log_message.emit("[ERROR] No device connected. Connect your AIO first.")
            return None
        if not self._lcd_controller or self._lcd_controller.device != device:
            self._lcd_controller = LCDController(device)
            self._lcd_controller.refresh_info()
        return self._lcd_controller

    def _toggle_live(self, checked: bool):
        if checked:
            controller = self._ensure_controller()
            if not controller:
                self.play_btn.setChecked(False)
                return
            self._scene_player = ScenePlayer(controller)
            cfg = self._build_scene_config()
            try:
                self._scene_player.start(cfg)
                self.play_btn.setText("Stop")
                self.log_message.emit("[OK] Screen Designer: live playback started")
            except Exception as e:
                self.log_message.emit(f"[ERROR] Could not start live playback: {e}")
                self.play_btn.setChecked(False)
                self._scene_player = None
        else:
            if self._scene_player:
                self._scene_player.stop()
                self._scene_player = None
            self.play_btn.setText("Start Live")
            self.log_message.emit("[OK] Screen Designer: live playback stopped")

    # ── Presets (save/load JSON) ─────────────────────────────

    def _save_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Preset", str(Path.home() / "scene.json"),
                                               "lovingcool-control scene (*.json)")
        if not path:
            return
        data = {
            "background": dataclasses.asdict(BackgroundConfig(
                kind=self._background_kind, path=self._background_path,
                color=(self._bg_color.red(), self._bg_color.green(), self._bg_color.blue()),
                fit_mode=self._fit_mode,
            )),
            "transparent_fill": (self._fill_color.red(), self._fill_color.green(), self._fill_color.blue()),
            "orientation": self._orientation,
            "rotation": self.rotation_spin.value(),
            "fps": self.fps_spin.value(),
            "widgets": [dataclasses.asdict(w) for w in self._widgets],
        }
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            self.log_message.emit(f"[OK] Saved preset: {Path(path).name}")
        except Exception as e:
            self.log_message.emit(f"[ERROR] Could not save preset: {e}")

    def _load_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Preset", str(Path.home()), "lovingcool-control scene (*.json)")
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)

            bg = data.get("background", {})
            self._background_kind = bg.get("kind", "color")
            self._background_path = bg.get("path")
            self._fit_mode = bg.get("fit_mode", "cover")
            r, g, b = bg.get("color", (14, 17, 20))
            self._bg_color = QColor(r, g, b)

            fr, fg, fb = data.get("transparent_fill", (14, 17, 20))
            self._fill_color = QColor(fr, fg, fb)

            self._widgets.clear()
            for wd in data.get("widgets", []):
                self._widgets.append(Widget(**wd))

            # Reflect loaded state into the UI (each setter re-renders the preview)
            type_labels = {"color": "Solid Color", "image": "Image", "gif": "GIF", "video": "Video"}
            self.bg_type_combo.setCurrentText(type_labels.get(self._background_kind, "Solid Color"))
            fit_index = self.fit_mode_combo.findData(self._fit_mode)
            if fit_index >= 0:
                self.fit_mode_combo.setCurrentIndex(fit_index)
            self.bg_file_field.setText(Path(self._background_path).name if self._background_path else "")
            self._update_color_buttons()

            orientation = data.get("orientation", "landscape")
            self._orientation = "portrait" if orientation == "portrait" else "landscape"
            self.btn_landscape.setChecked(self._orientation == "landscape")
            self.btn_portrait.setChecked(self._orientation == "portrait")

            self.rotation_spin.setValue(float(data.get("rotation", 0.0)))
            self.fps_spin.setValue(int(data.get("fps", 10)))
            self._sync_widgets_list()
            self._refresh_static_preview()
            self.log_message.emit(f"[OK] Loaded preset: {Path(path).name}")
        except Exception as e:
            self.log_message.emit(f"[ERROR] Could not load preset: {e}")
            QMessageBox.warning(self, "Load Preset Failed", str(e))

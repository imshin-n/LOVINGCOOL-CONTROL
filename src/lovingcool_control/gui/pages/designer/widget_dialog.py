"""Dialog for adding or editing one overlay widget (text, hardware stat, CPU graph)."""

from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
)

from lovingcool_control.core import sensors
from lovingcool_control.core.canvas import Widget

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


def describe_widget(w: Widget) -> str:
    if w.kind == "text":
        return f"Text @ ({w.x},{w.y}): \"{w.text}\""
    if w.kind == "stat":
        label = dict(sensors.METRIC_CHOICES).get(w.metric_key, w.metric_key)
        return f"Stat @ ({w.x},{w.y}): {label}"
    if w.kind == "cpu_graph":
        return f"CPU Graph @ ({w.x},{w.y}) {w.width}x{w.height}"
    return w.kind

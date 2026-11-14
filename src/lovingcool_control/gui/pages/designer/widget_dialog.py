"""Add / edit one scene widget: what it shows, where, and how it looks."""

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
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core import sensors
from lovingcool_control.core.canvas import Widget

KINDS = [("stat", "System stat"), ("text", "Text"), ("cpu_graph", "CPU graph")]
METRIC_NAMES = dict(sensors.METRIC_CHOICES)


def describe_widget(w: Widget) -> str:
    """One line for the designer's widget list."""
    at = f"({w.x}, {w.y})"
    if w.kind == "text":
        return f'Text "{w.text}"  {at}'
    if w.kind == "stat":
        return f"{METRIC_NAMES.get(w.metric_key, w.metric_key)}  {at}"
    if w.kind == "cpu_graph":
        return f"CPU graph {w.width}×{w.height}  {at}"
    return f"{w.kind}  {at}"


def _spin(lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    return s


class WidgetDialog(QDialog):
    def __init__(self, parent=None, initial: Widget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit widget" if initial else "Add widget")
        self.setMinimumWidth(340)
        start = initial or Widget(kind="stat")
        self._color = QColor(*start.color)

        outer = QVBoxLayout(self)
        top = QFormLayout()
        self.kind = QComboBox()
        for key, label in KINDS:
            self.kind.addItem(label, key)
        top.addRow("Type:", self.kind)
        outer.addLayout(top)

        # One page of options per kind
        self.pages = QStackedWidget()
        stat_page, stat_form = self._page()
        self.metric = QComboBox()
        for key, label in sensors.METRIC_CHOICES:
            self.metric.addItem(label, key)
        self.metric.setCurrentIndex(max(0, self.metric.findData(start.metric_key)))
        self.show_label = QCheckBox('Prefix with its name ("CPU: 42%")')
        self.show_label.setChecked(start.show_label)
        stat_form.addRow("Shows:", self.metric)
        stat_form.addRow("", self.show_label)

        text_page, text_form = self._page()
        self.text = QLineEdit(start.text)
        self.text.setPlaceholderText("Text to show")
        text_form.addRow("Text:", self.text)

        graph_page, graph_form = self._page()
        self.graph_w = _spin(20, 2000, start.width, " px")
        self.graph_h = _spin(20, 2000, start.height, " px")
        size_row = QHBoxLayout()
        size_row.addWidget(self.graph_w)
        size_row.addWidget(self.graph_h)
        graph_form.addRow("Size (W × H):", size_row)

        for page in (stat_page, text_page, graph_page):
            self.pages.addWidget(page)
        outer.addWidget(self.pages)

        common = QFormLayout()
        self.x = _spin(0, 4000, start.x)
        self.y = _spin(0, 4000, start.y)
        pos = QHBoxLayout()
        pos.addWidget(self.x)
        pos.addWidget(self.y)
        common.addRow("Position (X, Y):", pos)
        self.font_size = _spin(6, 300, start.font_size, " px")
        common.addRow("Text size:", self.font_size)
        self.color_btn = QPushButton()
        self.color_btn.clicked.connect(self._choose_color)
        common.addRow("Colour:", self.color_btn)
        outer.addLayout(common)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.kind.currentIndexChanged.connect(self.pages.setCurrentIndex)
        self.kind.setCurrentIndex(max(0, self.kind.findData(start.kind)))
        self.pages.setCurrentIndex(self.kind.currentIndex())
        self._show_color()

    @staticmethod
    def _page() -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        return page, form

    def _show_color(self) -> None:
        ink = "#000" if self._color.lightness() > 140 else "#fff"
        self.color_btn.setStyleSheet(f"background-color: {self._color.name()}; color: {ink};")
        self.color_btn.setText(self._color.name())

    def _choose_color(self) -> None:
        picked = QColorDialog.getColor(self._color, self, "Widget colour")
        if picked.isValid():
            self._color = picked
            self._show_color()

    def to_widget(self) -> Widget:
        return Widget(
            kind=self.kind.currentData(),
            x=self.x.value(),
            y=self.y.value(),
            font_size=self.font_size.value(),
            color=(self._color.red(), self._color.green(), self._color.blue()),
            text=self.text.text(),
            metric_key=self.metric.currentData() or "cpu_percent",
            show_label=self.show_label.isChecked(),
            width=self.graph_w.value(),
            height=self.graph_h.value(),
        )

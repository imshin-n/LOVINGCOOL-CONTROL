"""
The designer's canvas: shows the rendered scene and lets you pick widgets
with the mouse, drag them around, and delete the selected one with Delete.
Positions are edited in place on the bound widget list.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from lovingcool_control.core.canvas import Widget, widget_bbox

DEFAULT_CANVAS = (480, 320)   # shown until a panel size is known


class ScenePreview(QWidget):
    widget_moved = pyqtSignal()
    selection_changed = pyqtSignal(object)   # index, or None
    request_delete = pyqtSignal(int)

    def __init__(self, max_width: int = 380, max_height: int = 300):
        super().__init__()
        self.setFixedSize(max_width, max_height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.snap = 0                     # grid size in canvas pixels; 0 = free movement
        self._image: QImage | None = None
        self._canvas = DEFAULT_CANVAS
        self._widgets: list[Widget] = []
        self._metrics: dict = {}
        self._selected: int | None = None
        self._grab: tuple[int, float, float] | None = None   # (index, dx, dy) while dragging

    # ── Data ──────────────────────────────────────────────────

    def bind_widgets(self, widgets: list[Widget]) -> None:
        self._widgets = widgets

    @property
    def selected_index(self) -> int | None:
        return self._selected

    def select(self, index: int | None, emit: bool = True) -> None:
        self._selected = index
        if emit:
            self.selection_changed.emit(index)

    def set_image(self, img, metrics: dict | None = None) -> None:
        """Show a rendered frame (a PIL image at the logical canvas size)."""
        if metrics is not None:
            self._metrics = metrics
        rgb = img.convert("RGB")
        self._canvas = rgb.size
        self._image = QImage(rgb.tobytes(), rgb.width, rgb.height, 3 * rgb.width,
                             QImage.Format.Format_RGB888).copy()
        self.update()

    # ── Geometry ──────────────────────────────────────────────

    def _target(self) -> QRectF:
        """Where the canvas is drawn: as large as fits, centred, aspect kept."""
        cw, ch = self._canvas
        margin = 6
        scale = min((self.width() - 2 * margin) / cw, (self.height() - 2 * margin) / ch)
        w, h = cw * scale, ch * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_canvas(self, pos: QPointF) -> tuple[float, float] | None:
        r = self._target()
        if not r.contains(pos):
            return None
        return ((pos.x() - r.left()) * self._canvas[0] / r.width(),
                (pos.y() - r.top()) * self._canvas[1] / r.height())

    def _widget_at(self, x: float, y: float) -> int | None:
        for i in range(len(self._widgets) - 1, -1, -1):   # topmost (last drawn) first
            left, top, right, bottom = widget_bbox(self._widgets[i], self._metrics)
            if left <= x <= right and top <= y <= bottom:
                return i
        return None

    # ── Painting ──────────────────────────────────────────────

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#140c0c"))
        target = self._target()
        if self._image is None:
            p.setPen(QColor("#9C8A82"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No preview")
        else:
            p.drawImage(target, self._image)
        p.setPen(QPen(QColor("#3a2d2b"), 1))
        p.drawRect(target.adjusted(-1, -1, 0, 0))
        p.end()

    # ── Mouse and keyboard ────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        point = self._to_canvas(event.position())
        index = self._widget_at(*point) if point else None
        self.select(index)
        if index is not None:
            w = self._widgets[index]
            self._grab = (index, point[0] - w.x, point[1] - w.y)

    def mouseMoveEvent(self, event) -> None:
        if self._grab is None:
            hovering = (p := self._to_canvas(event.position())) is not None and self._widget_at(*p) is not None
            self.setCursor(Qt.CursorShape.OpenHandCursor if hovering else Qt.CursorShape.ArrowCursor)
            return
        point = self._to_canvas(event.position())
        if point is None:
            return
        index, dx, dy = self._grab
        x, y = point[0] - dx, point[1] - dy
        if self.snap:
            x, y = round(x / self.snap) * self.snap, round(y / self.snap) * self.snap
        w = self._widgets[index]
        w.x = int(min(max(x, 0), self._canvas[0]))
        w.y = int(min(max(y, 0), self._canvas[1]))
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.widget_moved.emit()

    def mouseReleaseEvent(self, event) -> None:
        self._grab = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._selected is not None:
            index = self._selected
            self.select(None)
            self.request_delete.emit(index)
            return
        super().keyPressEvent(event)

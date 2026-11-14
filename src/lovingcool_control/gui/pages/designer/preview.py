"""Interactive scene preview: click to select a widget, drag to move it, Delete to remove it."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLabel

from lovingcool_control.core.canvas import Widget, widget_bbox

DEFAULT_CANVAS = (480, 320)   # until a panel size is known


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

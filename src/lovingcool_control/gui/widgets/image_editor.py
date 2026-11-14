"""
Crop and touch up a picture inside the app, so nobody has to prepare it in
another editor first. Drag the box to move it, drag its corners to resize
it; the box keeps the screen's shape unless "Free" is chosen. The result
is saved as an edited copy (see core/image_edit.py); the original file is
left alone.
"""

from __future__ import annotations

import logging

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lovingcool_control.core.image_edit import (
    ImageEdits,
    adjust,
    edits_for,
    is_animated,
    open_upright,
    save_edited,
    turn,
)

log = logging.getLogger(__name__)

WORKING_SIDE = 900      # the editor works on a copy this size; the saved file uses the full original
HANDLE = 9              # corner grab size in screen pixels
MIN_CROP = 12           # smallest crop side in working-image pixels


def _qimage(img: Image.Image) -> QImage:
    rgb = img.convert("RGB")
    return QImage(rgb.tobytes(), rgb.width, rgb.height, 3 * rgb.width, QImage.Format.Format_RGB888).copy()


class CropCanvas(QWidget):
    """Shows the picture with a crop box over it. The box is kept in image pixels."""

    crop_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 340)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._image: QImage | None = None
        self._size = (1, 1)
        self.ratio: float | None = None          # width / height of the box, or None for free
        self.box = QRectF()
        self._drag: tuple[str, QPointF, QRectF] | None = None   # (what, start point, box at start)

    def sizeHint(self) -> QSize:
        return QSize(640, 480)

    # ── Data ──────────────────────────────────────────────────

    def set_image(self, img: Image.Image, keep_rect: bool = True) -> None:
        resized = img.size != self._size
        self._image = _qimage(img)
        self._size = img.size
        if resized or not keep_rect or self.box.isEmpty():
            self.reset_crop()
        self.update()

    def set_ratio(self, ratio: float | None) -> None:
        self.ratio = ratio
        self._fit_ratio()

    def reset_crop(self) -> None:
        """The largest box of the chosen shape, centred."""
        w, h = self._size
        if self.ratio is None:
            self.box = QRectF(0, 0, w, h)
        else:
            bw, bh = (w, w / self.ratio) if w / h < self.ratio else (h * self.ratio, h)
            self.box = QRectF((w - bw) / 2, (h - bh) / 2, bw, bh)
        self.update()
        self.crop_changed.emit()

    def _fit_ratio(self) -> None:
        """Make the current box the chosen shape, keeping its centre and staying inside the picture."""
        if self.ratio is None or self.box.isEmpty():
            self.update()
            self.crop_changed.emit()
            return
        c = self.box.center()
        w, h = self.box.width(), self.box.height()
        w, h = (w, w / self.ratio) if w / h < self.ratio else (h * self.ratio, h)
        iw, ih = self._size
        scale = min(1.0, iw / w, ih / h)
        w, h = w * scale, h * scale
        x = min(max(c.x() - w / 2, 0), iw - w)
        y = min(max(c.y() - h / 2, 0), ih - h)
        self.box = QRectF(x, y, w, h)
        self.update()
        self.crop_changed.emit()

    def set_crop_fractions(self, crop) -> None:
        w, h = self._size
        left, top, right, bottom = crop
        self.box = QRectF(left * w, top * h, (right - left) * w, (bottom - top) * h)
        self._fit_ratio()

    def crop_fractions(self) -> tuple[float, float, float, float]:
        w, h = self._size
        r = self.box
        return r.left() / w, r.top() / h, r.right() / w, r.bottom() / h

    def crop_pixels(self, full_size: tuple[int, int]) -> tuple[int, int]:
        """How large the crop will be in the saved picture."""
        left, top, right, bottom = self.crop_fractions()
        return round((right - left) * full_size[0]), round((bottom - top) * full_size[1])

    # ── Geometry ──────────────────────────────────────────────

    def _view(self) -> tuple[QRectF, float]:
        """Where the picture is drawn and its scale."""
        w, h = self._size
        margin = 12
        scale = min((self.width() - 2 * margin) / w, (self.height() - 2 * margin) / h)
        dw, dh = w * scale, h * scale
        return QRectF((self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh), scale

    def _to_image(self, p: QPointF) -> QPointF:
        view, scale = self._view()
        return QPointF((p.x() - view.left()) / scale, (p.y() - view.top()) / scale)

    def _on_screen(self, r: QRectF) -> QRectF:
        view, scale = self._view()
        return QRectF(view.left() + r.left() * scale, view.top() + r.top() * scale,
                      r.width() * scale, r.height() * scale)

    def _hit(self, pos: QPointF) -> str | None:
        box = self._on_screen(self.box)
        corners = {"tl": box.topLeft(), "tr": box.topRight(), "bl": box.bottomLeft(), "br": box.bottomRight()}
        for name, c in corners.items():
            if abs(pos.x() - c.x()) <= HANDLE and abs(pos.y() - c.y()) <= HANDLE:
                return name
        return "move" if box.contains(pos) else None

    # ── Painting ──────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#140c0c"))
        if self._image is None:
            p.end()
            return
        view, _ = self._view()
        p.drawImage(view, self._image)

        box = self._on_screen(self.box)
        shade = QPainterPath()
        shade.addRect(view)
        hole = QPainterPath()
        hole.addRect(box)
        p.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 150))

        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for i in (1, 2):   # rule-of-thirds guides
            x = box.left() + box.width() * i / 3
            y = box.top() + box.height() * i / 3
            p.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            p.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
        p.setPen(QPen(QColor("#C8743E"), 2))
        p.drawRect(box)
        p.setBrush(QColor("#C8743E"))
        for c in (box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight()):
            p.drawRect(QRectF(c.x() - HANDLE / 2, c.y() - HANDLE / 2, HANDLE, HANDLE))
        p.end()

    # ── Mouse ─────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        what = self._hit(event.position())
        if what:
            self._drag = (what, self._to_image(event.position()), QRectF(self.box))

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            what = self._hit(event.position())
            cursor = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                      "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                      "move": Qt.CursorShape.SizeAllCursor}.get(what, Qt.CursorShape.ArrowCursor)
            self.setCursor(cursor)
            return
        what, start, original = self._drag
        point = self._to_image(event.position())
        if what == "move":
            self._move(original, point.x() - start.x(), point.y() - start.y())
        else:
            self._resize(what, original, point)
        self.update()
        self.crop_changed.emit()

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_crop()

    def _move(self, original: QRectF, dx: float, dy: float) -> None:
        iw, ih = self._size
        x = min(max(original.left() + dx, 0), iw - original.width())
        y = min(max(original.top() + dy, 0), ih - original.height())
        self.box = QRectF(x, y, original.width(), original.height())

    def _resize(self, corner: str, original: QRectF, point: QPointF) -> None:
        """Drag one corner; the opposite corner stays put."""
        iw, ih = self._size
        ax = original.right() if "l" in corner else original.left()
        ay = original.bottom() if "t" in corner else original.top()
        sx = -1 if "l" in corner else 1
        sy = -1 if "t" in corner else 1
        room_w = ax if sx < 0 else iw - ax
        room_h = ay if sy < 0 else ih - ay
        w = min(max((point.x() - ax) * sx, MIN_CROP), room_w)
        h = min(max((point.y() - ay) * sy, MIN_CROP), room_h)
        if self.ratio is not None:
            w = max(w, h * self.ratio)
            h = w / self.ratio
            if w > room_w:
                w, h = room_w, room_w / self.ratio
            if h > room_h:
                h, w = room_h, room_h * self.ratio
        x = ax - w if sx < 0 else ax
        y = ay - h if sy < 0 else ay
        self.box = QRectF(x, y, w, h)


class ImageEditorDialog(QDialog):
    """
    Edit a picture for the screen. screen_size (width, height) sets the
    default crop shape. After exec() succeeds, result_path is the edited copy.
    """

    def __init__(self, path: str, screen_size: tuple[int, int] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit image")
        self.result_path: str | None = None
        self._screen = screen_size
        self.edits: ImageEdits = edits_for(path)
        source = open_upright(self.edits.source)   # the first frame, for a GIF
        self._animated = is_animated(self.edits.source)
        self._full = source.convert("RGBA") if source.mode in ("RGBA", "LA", "P") else source.convert("RGB")
        self._base = self._full.copy()
        self._base.thumbnail((WORKING_SIDE, WORKING_SIDE), Image.LANCZOS)

        self._build()
        self._adjust_timer = QTimer(self, singleShot=True, interval=40)
        self._adjust_timer.timeout.connect(self._redraw)
        self._load(self.edits)

    # ── Layout ────────────────────────────────────────────────

    def _build(self) -> None:
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.canvas = CropCanvas()
        self.canvas.crop_changed.connect(self._show_size)
        left.addWidget(self.canvas, stretch=1)
        hint = QLabel("Drag the box to move it, drag a corner to resize it, double-click to reset.")
        hint.setStyleSheet("color: #9C8A82;")
        left.addWidget(hint)
        root.addLayout(left, stretch=1)

        side = QVBoxLayout()
        crop_box = QGroupBox("Crop")
        cf = QFormLayout(crop_box)
        self.shape = QComboBox()
        if self._screen:
            w, h = self._screen
            self.shape.addItem(f"Screen ({w} × {h})", w / h)
        self.shape.addItem("Square", 1.0)
        self.shape.addItem("Original shape", "original")
        self.shape.addItem("Free", "free")
        self.shape.currentIndexChanged.connect(self._on_shape)
        cf.addRow("Shape:", self.shape)
        self.lbl_size = QLabel()
        cf.addRow("Result:", self.lbl_size)
        reset_crop = QPushButton("Whole picture")
        reset_crop.clicked.connect(self.canvas.reset_crop)
        cf.addRow("", reset_crop)
        side.addWidget(crop_box)

        turn_box = QGroupBox("Rotate and flip")
        tl = QHBoxLayout(turn_box)
        for text, tip, slot in (("⟲", "Rotate left", lambda: self._rotate(-90)),
                                ("⟳", "Rotate right", lambda: self._rotate(90)),
                                ("⇋", "Flip left to right", lambda: self._flip(horizontal=True)),
                                ("⇵", "Flip top to bottom", lambda: self._flip(horizontal=False))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setMinimumWidth(40)
            b.clicked.connect(slot)
            tl.addWidget(b)
        side.addWidget(turn_box)

        adjust_box = QGroupBox("Adjust")
        af = QFormLayout(adjust_box)
        self.sliders: dict[str, QSlider] = {}
        for key, label in (("brightness", "Brightness"), ("contrast", "Contrast"), ("saturation", "Saturation")):
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(-100, 100)
            s.setPageStep(10)
            s.valueChanged.connect(lambda _v: self._adjust_timer.start())
            self.sliders[key] = s
            af.addRow(f"{label}:", s)
        reset_adjust = QPushButton("Reset adjustments")
        reset_adjust.clicked.connect(self._reset_adjustments)
        af.addRow("", reset_adjust)
        side.addWidget(adjust_box)

        if self._animated:
            note = QLabel("Animated GIF: the edits are applied to every frame.")
            note.setWordWrap(True)
            side.addWidget(note)
        side.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Use edited image")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        side.addWidget(buttons)
        root.addLayout(side)
        self.resize(980, 600)

    # ── State ─────────────────────────────────────────────────

    def _load(self, edits: ImageEdits) -> None:
        for key, s in self.sliders.items():
            s.blockSignals(True)
            s.setValue(round((getattr(edits, key) - 1.0) * 100))
            s.blockSignals(False)
        self.canvas.ratio = self._shape_ratio()
        self._redraw(keep_rect=False)
        if edits.crop is not None:
            # Saved crops are restored as they were, so a free crop isn't forced back to the screen shape
            self._select_shape_matching(edits)
            self.canvas.set_crop_fractions(edits.crop)

    def _select_shape_matching(self, edits: ImageEdits) -> None:
        w, h = self._turned_size()
        left, top, right, bottom = edits.crop
        ratio = (right - left) * w / max(1e-6, (bottom - top) * h)
        for i in range(self.shape.count()):
            want = self._shape_ratio(i)
            if want is not None and abs(want - ratio) < 0.01:
                self.shape.blockSignals(True)
                self.shape.setCurrentIndex(i)
                self.shape.blockSignals(False)
                self.canvas.ratio = want
                return
        self.shape.blockSignals(True)
        self.shape.setCurrentIndex(self.shape.findData("free"))
        self.shape.blockSignals(False)
        self.canvas.ratio = None

    def _turned_size(self) -> tuple[int, int]:
        w, h = self._full.size
        return (h, w) if self.edits.rotate % 180 else (w, h)

    def _shape_ratio(self, index: int | None = None) -> float | None:
        data = self.shape.itemData(self.shape.currentIndex() if index is None else index)
        if data == "original":
            w, h = self._turned_size()
            return w / h
        return None if data == "free" else float(data)

    def _current_edits(self) -> ImageEdits:
        values = {k: 1.0 + s.value() / 100.0 for k, s in self.sliders.items()}
        return ImageEdits(source=self.edits.source, rotate=self.edits.rotate, flip_h=self.edits.flip_h,
                          flip_v=self.edits.flip_v, crop=self.canvas.crop_fractions(), **values)

    def _redraw(self, keep_rect: bool = True) -> None:
        current = self._current_edits()
        self.canvas.set_image(adjust(turn(self._base, current), current), keep_rect=keep_rect)
        self._show_size()

    def _show_size(self) -> None:
        w, h = self.canvas.crop_pixels(self._turned_size())
        self.lbl_size.setText(f"{w} × {h} px")

    def _on_shape(self) -> None:
        self.canvas.set_ratio(self._shape_ratio())

    def _rotate(self, degrees: int) -> None:
        crop = self.canvas.crop_fractions()
        left, top, right, bottom = crop
        if degrees > 0:   # clockwise: (x, y) -> (1 - y, x)
            crop = (1 - bottom, left, 1 - top, right)
        else:             # anticlockwise: (x, y) -> (y, 1 - x)
            crop = (top, 1 - right, bottom, 1 - left)
        self.edits.rotate = (self.edits.rotate + degrees) % 360
        self._redraw()
        self.canvas.ratio = self._shape_ratio()
        self.canvas.set_crop_fractions(crop)

    def _flip(self, horizontal: bool) -> None:
        left, top, right, bottom = self.canvas.crop_fractions()
        if horizontal:
            self.edits.flip_h = not self.edits.flip_h
            crop = (1 - right, top, 1 - left, bottom)
        else:
            self.edits.flip_v = not self.edits.flip_v
            crop = (left, 1 - bottom, right, 1 - top)
        self._redraw()
        self.canvas.set_crop_fractions(crop)

    def _reset_adjustments(self) -> None:
        for s in self.sliders.values():
            s.blockSignals(True)
            s.setValue(0)
            s.blockSignals(False)
        self._redraw()

    # ── Save ──────────────────────────────────────────────────

    def _save(self) -> None:
        edits = self._current_edits()
        if edits.crop is not None and all(abs(a - b) < 1e-4 for a, b in zip(edits.crop, (0, 0, 1, 1), strict=True)):
            edits.crop = None
        try:
            self.result_path = str(save_edited(edits))
        except Exception as e:
            log.error("Saving the edited image failed: %s", e)
            QMessageBox.warning(self, "Edit image", f"Couldn't save the edited image:\n{e}")
            return
        self.edits = edits
        self.accept()

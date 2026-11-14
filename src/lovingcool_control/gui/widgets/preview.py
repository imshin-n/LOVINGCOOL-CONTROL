"""A framed, aspect-correct picture of what the cooler's screen will show."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel


class ScreenPreview(QLabel):
    def __init__(self, size: int = 300):
        super().__init__()
        self._box = size
        self.setFixedSize(size, size)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background-color: #000; color: #9C8A82;")   # the panel itself is black when off
        self.setText("No preview")

    def set_image(self, img) -> None:
        """Show a PIL image scaled to fit, keeping its aspect ratio."""
        img = img.convert("RGB")
        inner = self._box - 2 * self.frameWidth()
        scale = min(inner / img.width, inner / img.height)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
        self.setPixmap(QPixmap.fromImage(qimg.copy()))

    def clear_image(self, text: str = "") -> None:
        self.setPixmap(QPixmap())
        self.setText(text)

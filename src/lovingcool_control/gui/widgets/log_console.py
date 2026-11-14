"""
lovingcool-control :: gui/widgets/log_console.py
=========================================
Floating log console that can be docked at the bottom of any page.
Shows timestamped messages with colour-coded severity.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

COLORS = {
    "[OK]":    "#5BC97B",
    "[ERROR]": "#E0556B",
    "[WARN]":  "#E8A33D",
    "[INFO]":  "#8B93A1",
    "[TX]":    "#3DD6C8",
    "[RX]":    "#9B77FF",
}


class LogConsole(QWidget):
    def __init__(self, title: str = "Log"):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        label = QLabel(title.upper())
        label.setObjectName("sectionLabel")
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(22)
        clear_btn.setStyleSheet("font-size: 11px; padding: 0 8px;")
        clear_btn.clicked.connect(self.clear)
        header.addWidget(label)
        header.addStretch()
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self._text = QTextEdit()
        self._text.setObjectName("logConsole")
        self._text.setReadOnly(True)
        self._text.setMaximumHeight(130)
        layout.addWidget(self._text)

    def append(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        # Detect severity tag
        color = "#E8EBF0"
        for tag, c in COLORS.items():
            if tag in message:
                color = c
                break
        html = (
            f'<span style="color:#8B93A1">[{ts}]</span> '
            f'<span style="color:{color}">{message}</span>'
        )
        self._text.append(html)
        self._text.moveCursor(QTextCursor.MoveOperation.End)

    def clear(self):
        self._text.clear()

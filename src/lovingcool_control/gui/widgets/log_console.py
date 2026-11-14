"""
lovingcool-control :: gui/widgets/log_console.py
=========================================
Floating log console that can be docked at the bottom of any page.
Shows timestamped messages with colour-coded severity.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from lovingcool_control.gui.theme import DANGER, MUTED, SUCCESS, WARNING

COLORS = {"[OK]": SUCCESS, "[ERROR]": DANGER, "[WARN]": WARNING}


class LogConsole(QWidget):
    def __init__(self, title: str = "Log"):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        label = QLabel(title)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        header.addWidget(label)
        header.addStretch()
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        layout.addWidget(self._text)

    def append(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        # Detect severity tag
        color = next((c for tag, c in COLORS.items() if tag in message), None)
        body = escape(message)
        if color:
            body = f'<span style="color:{color}">{body}</span>'
        html = f'<span style="color:{MUTED}">[{ts}]</span> {body}'   # default text uses the palette
        self._text.append(html)
        self._text.moveCursor(QTextCursor.MoveOperation.End)

    def clear(self):
        self._text.clear()

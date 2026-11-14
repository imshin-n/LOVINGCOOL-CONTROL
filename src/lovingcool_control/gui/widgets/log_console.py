"""The event log on the Information tab: timestamped lines, coloured by their [OK]/[WARN]/[ERROR] tag."""

from __future__ import annotations

import time
from html import escape

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from lovingcool_control.gui.theme import DANGER, MUTED, SUCCESS, WARNING

TAG_COLORS = (("[ERROR]", DANGER), ("[WARN]", WARNING), ("[OK]", SUCCESS))
MAX_LINES = 2000


class LogConsole(QWidget):
    def __init__(self, title: str = "Log"):
        super().__init__()
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)   # oldest lines drop off; memory stays bounded

        bar = QHBoxLayout()
        bar.addWidget(QLabel(title), stretch=1)
        wipe = QPushButton("Clear")
        wipe.clicked.connect(self.view.clear)
        bar.addWidget(wipe)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addLayout(bar)
        column.addWidget(self.view)

    def append(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        body = escape(message)
        color = next((c for tag, c in TAG_COLORS if message.startswith(tag)), None)
        if color:
            body = f'<span style="color:{color}">{body}</span>'
        self.view.appendHtml(f'<span style="color:{MUTED}">{stamp}</span>&nbsp; {body}')

    def clear(self) -> None:
        self.view.clear()

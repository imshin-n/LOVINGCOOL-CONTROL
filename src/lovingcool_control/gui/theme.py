"""
lovingcool-control :: gui/theme.py
==================================
Plain Qt look: the Fusion style with a light or dark palette, no custom
stylesheet over the widgets. "auto" follows the desktop's colour scheme
(Qt 6.5+), falling back to light.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

THEMES = ("auto", "light", "dark")

# Status colours, readable on both palettes
SUCCESS = "#3BA55C"
WARNING = "#D08A1E"
DANGER = "#D6455D"
MUTED = "#808890"


def _dark_palette() -> QPalette:
    p = QPalette()
    window, base, alt, text = QColor(53, 53, 53), QColor(35, 35, 35), QColor(45, 45, 45), QColor(220, 220, 220)
    disabled = QColor(127, 127, 127)
    highlight = QColor(42, 130, 218)
    roles = {
        QPalette.ColorRole.Window: window,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: base,
        QPalette.ColorRole.AlternateBase: alt,
        QPalette.ColorRole.ToolTipBase: window,
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: disabled,
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: window,
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.BrightText: QColor(255, 80, 80),
        QPalette.ColorRole.Link: highlight,
        QPalette.ColorRole.Highlight: highlight,
        QPalette.ColorRole.HighlightedText: QColor(255, 255, 255),
        QPalette.ColorRole.Light: QColor(70, 70, 70),
        QPalette.ColorRole.Midlight: QColor(60, 60, 60),
        QPalette.ColorRole.Mid: QColor(40, 40, 40),
        QPalette.ColorRole.Dark: QColor(25, 25, 25),
        QPalette.ColorRole.Shadow: QColor(15, 15, 15),
    }
    for role, color in roles.items():
        p.setColor(role, color)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.HighlightedText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(80, 80, 80))
    return p


def system_prefers_dark(app: QApplication) -> bool:
    hints = app.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    return bool(scheme and scheme() == Qt.ColorScheme.Dark)


def apply_theme(app: QApplication, theme: str = "auto") -> str:
    """Apply Fusion + palette; returns the resolved theme ("light" or "dark")."""
    app.setStyle("Fusion")
    dark = theme == "dark" or (theme == "auto" and system_prefers_dark(app))
    app.setPalette(_dark_palette() if dark else app.style().standardPalette())
    app.setStyleSheet("")
    return "dark" if dark else "light"

"""
lovingcool-control :: gui/theme.py
==================================
Plain Qt look: the Fusion style with a light or dark palette and a single
stylesheet rule for tab panels. "auto" follows the desktop's colour scheme
(Qt 6.5+), falling back to light. Both palettes are warm (brown-black /
cream with a copper accent).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

THEMES = ("auto", "light", "dark")

# Status colours, readable on both palettes
SUCCESS = "#6DBE6A"
WARNING = "#E0A040"
DANGER = "#E0604F"
MUTED = "#9C8A82"

# Warm palette. Dark: #1b1111 for the window frame, bars and fields, #231a1a
# for tab panels, with a copper accent. Light: the same hues as cream and
# warm greys.
ACCENT = "#C8743E"

DARK = {
    "window": "#1b1111", "base": "#1b1111", "alt": "#261b1b", "button": "#2e2322", "pane": "#231a1a",
    "border": "#3a2d2b",
    "text": "#EADBD2", "disabled": "#7d6c66", "placeholder": "#8a7770",
    "light": "#4a3a37", "midlight": "#3a2d2b", "mid": "#1f1616", "dark": "#140c0c", "shadow": "#0b0606",
    "highlight": ACCENT, "highlighted_text": "#ffffff", "link": "#E39A63",
}
LIGHT = {
    "window": "#EFE4DC", "base": "#FCF8F4", "alt": "#F1E6DE", "button": "#EBDFD6", "pane": "#F8F1EB",
    "border": "#CDBAAE",
    "text": "#2B1E1B", "disabled": "#A8968E", "placeholder": "#9A877F",
    "light": "#FFFFFF", "midlight": "#F6EEE8", "mid": "#C9B6AA", "dark": "#A8938A", "shadow": "#6E5C55",
    "highlight": ACCENT, "highlighted_text": "#ffffff", "link": "#A9562A",
}


def _palette(c: dict[str, str]) -> QPalette:
    R = QPalette.ColorRole
    p = QPalette()
    roles = {
        R.Window: "window", R.WindowText: "text", R.Base: "base", R.AlternateBase: "alt",
        R.ToolTipBase: "button", R.ToolTipText: "text", R.PlaceholderText: "placeholder",
        R.Text: "text", R.Button: "button", R.ButtonText: "text", R.BrightText: "highlighted_text",
        R.Link: "link", R.Highlight: "highlight", R.HighlightedText: "highlighted_text",
        R.Light: "light", R.Midlight: "midlight", R.Mid: "mid", R.Dark: "dark", R.Shadow: "shadow",
    }
    for role, key in roles.items():
        p.setColor(role, QColor(c[key]))
    for role in (R.Text, R.ButtonText, R.WindowText, R.HighlightedText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["disabled"]))
    p.setColor(QPalette.ColorGroup.Disabled, R.Highlight, QColor(c["mid"]))
    return p


def _panel_css(c: dict[str, str]) -> str:
    # Fusion fills tab panes with a lightened window colour; pin them to the
    # palette's panel colour so the reference shades stay exact.
    return (f"QTabWidget::pane {{ background: {c['pane']}; border: 1px solid {c['border']}; }}"
            f"QTabWidget QTabWidget::pane {{ background: {c['pane']}; }}")


def system_prefers_dark(app: QApplication) -> bool:
    hints = app.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    return bool(scheme and scheme() == Qt.ColorScheme.Dark)


def apply_theme(app: QApplication, theme: str = "auto") -> str:
    """Apply Fusion + palette; returns the resolved theme ("light" or "dark")."""
    app.setStyle("Fusion")
    dark = theme == "dark" or (theme == "auto" and system_prefers_dark(app))
    colors = DARK if dark else LIGHT
    app.setPalette(_palette(colors))
    app.setStyleSheet(_panel_css(colors))
    return "dark" if dark else "light"

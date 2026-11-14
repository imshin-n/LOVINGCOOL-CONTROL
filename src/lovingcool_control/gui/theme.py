"""
lovingcool-control :: gui/theme.py
==========================
Centralized visual identity for the app — a dark, "lab instrument"
aesthetic fitting a hardware-control tool: deep graphite background,
a cold cyan accent reminiscent of liquid cooling, monospace readouts
for raw protocol data, and a warm amber for warnings/RGB previews.
"""

# ── Palette ──────────────────────────────────────────────────
BG_BASE      = "#14171C"   # near-black graphite
BG_PANEL     = "#1B1F26"   # raised panel
BG_PANEL_ALT = "#21262F"   # input fields / hover
BORDER       = "#2C323D"
TEXT_PRIMARY = "#E8EBF0"
TEXT_MUTED   = "#8B93A1"
ACCENT_CYAN  = "#3DD6C8"   # liquid-cool accent
ACCENT_CYAN_DIM = "#2A7A72"
ACCENT_AMBER = "#E8A33D"   # warnings / RGB indicator
DANGER       = "#E0556B"
SUCCESS      = "#5BC97B"

FONT_UI = "Inter, 'Segoe UI', -apple-system, sans-serif"
FONT_MONO = "'JetBrains Mono', 'Cascadia Code', Consolas, monospace"

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG_BASE};
    color: {TEXT_PRIMARY};
    font-family: {FONT_UI};
    font-size: 13px;
}}

QWidget#sidebar {{
    background-color: {BG_PANEL};
    border-right: 1px solid {BORDER};
}}

QLabel#brandTitle {{
    color: {ACCENT_CYAN};
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 18px 16px 4px 16px;
}}

QLabel#brandSubtitle {{
    color: {TEXT_MUTED};
    font-size: 11px;
    padding: 0px 16px 16px 16px;
}}

QPushButton#navButton {{
    background-color: transparent;
    color: {TEXT_MUTED};
    border: none;
    border-left: 3px solid transparent;
    text-align: left;
    padding: 12px 16px;
    font-size: 13px;
    font-weight: 500;
}}

QPushButton#navButton:hover {{
    background-color: {BG_PANEL_ALT};
    color: {TEXT_PRIMARY};
}}

QPushButton#navButton:checked {{
    background-color: {BG_PANEL_ALT};
    color: {ACCENT_CYAN};
    border-left: 3px solid {ACCENT_CYAN};
}}

QWidget#statusBar {{
    background-color: {BG_PANEL};
    border-top: 1px solid {BORDER};
}}

QLabel#statusDot {{
    font-size: 14px;
}}

QLabel#statusText {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}

QFrame#panel {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

QLabel#pageTitle {{
    font-size: 20px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
}}

QLabel#pageSubtitle {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}

QLabel#sectionLabel {{
    color: {TEXT_MUTED};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}}

QPushButton {{
    background-color: {BG_PANEL_ALT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 600;
}}

QPushButton:hover {{
    border-color: {ACCENT_CYAN_DIM};
}}

QPushButton:pressed {{
    background-color: {BORDER};
}}

QPushButton#primaryButton {{
    background-color: {ACCENT_CYAN};
    color: {BG_BASE};
    border: none;
}}

QPushButton#primaryButton:hover {{
    background-color: #4FE3D5;
}}

QPushButton#dangerButton {{
    background-color: transparent;
    color: {DANGER};
    border: 1px solid {DANGER};
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {ACCENT_CYAN};
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}

QSlider::sub-page:horizontal {{
    background: {ACCENT_CYAN_DIM};
    border-radius: 2px;
}}

QComboBox, QLineEdit, QSpinBox {{
    background-color: {BG_PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
}}

QComboBox:hover, QLineEdit:hover {{
    border-color: {ACCENT_CYAN_DIM};
}}

QTextEdit#logConsole {{
    background-color: #0E1014;
    color: {SUCCESS};
    font-family: {FONT_MONO};
    font-size: 11px;
    border: 1px solid {BORDER};
    border-radius: 6px;
}}

QProgressBar {{
    background-color: {BG_PANEL_ALT};
    border: none;
    border-radius: 4px;
    height: 8px;
}}

QProgressBar::chunk {{
    background-color: {ACCENT_CYAN};
    border-radius: 4px;
}}

QScrollBar:vertical {{
    background: {BG_BASE};
    width: 10px;
}}

QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: {ACCENT_CYAN_DIM};
}}
"""

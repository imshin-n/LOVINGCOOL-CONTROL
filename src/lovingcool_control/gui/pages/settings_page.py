"""Settings tab: appearance and where things are."""

from __future__ import annotations

from PyQt6.QtCore import QSettings, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget

from lovingcool_control.core.device import user_config_dir

THEME_LABELS = [("auto", "Follow system"), ("light", "Light"), ("dark", "Dark")]


class SettingsPage(QWidget):
    theme_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._settings = QSettings("lovingcool-control", "lovingcool-control")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        look = QGroupBox("Appearance")
        f = QFormLayout(look)
        self.theme = QComboBox()
        for key, label in THEME_LABELS:
            self.theme.addItem(label, key)
        current = self._settings.value("ui/theme", "auto")
        self.theme.setCurrentIndex(max(0, self.theme.findData(current)))
        self.theme.currentIndexChanged.connect(self._on_theme)
        f.addRow("Theme:", self.theme)
        root.addWidget(look)

        files = QGroupBox("Files")
        f2 = QFormLayout(files)
        path = QLabel(str(user_config_dir()))
        f2.addRow("Settings and scenes:", path)
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(self._open_config)
        f2.addRow("", open_btn)
        root.addWidget(files)
        root.addStretch()

    def _on_theme(self) -> None:
        key = self.theme.currentData()
        self._settings.setValue("ui/theme", key)
        self.theme_changed.emit(key)

    def _open_config(self) -> None:
        user_config_dir().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_config_dir())))

"""
Tabs down the left side with horizontal, readable labels (and icons),
instead of Qt's default sideways text for West-positioned tabs.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWidgets import QStyle, QStyleOptionTab, QStylePainter, QTabBar, QTabWidget


class HorizontalTabBar(QTabBar):
    MIN_WIDTH = 150

    def tabSizeHint(self, index: int) -> QSize:
        size = super().tabSizeHint(index)
        size.transpose()   # the West bar measures tabs rotated; swap back to a wide, short tab
        return QSize(max(size.width() + 16, self.MIN_WIDTH), max(size.height(), 36))

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        opt = QStyleOptionTab()
        for i in range(self.count()):
            self.initStyleOption(opt, i)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, opt)
            painter.save()
            # Draw the label as if the tab were a normal top tab, rotated back upright.
            size = opt.rect.size()
            size.transpose()
            rect = QRect(QPoint(), size)
            rect.moveCenter(opt.rect.center())
            opt.rect = rect
            center = self.tabRect(i).center()
            painter.translate(center)
            painter.rotate(90)
            painter.translate(-center)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabLabel, opt)
            painter.restore()


def side_tabs() -> QTabWidget:
    tabs = QTabWidget()
    tabs.setTabBar(HorizontalTabBar())
    tabs.setTabPosition(QTabWidget.TabPosition.West)
    tabs.setIconSize(QSize(20, 20))
    return tabs

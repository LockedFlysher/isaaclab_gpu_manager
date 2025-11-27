from __future__ import annotations

from typing import Dict, List, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class PieChartWidget(QWidget):
    """A lightweight pie chart widget drawn with QPainter.

    set_data expects a mapping of label->value (float or int). Values <= 0 are ignored.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: List[Tuple[str, float]] = []
        # A fixed color palette; will cycle if more slices than colors
        self._colors = [
            QColor(0x4e79a7), QColor(0xf28e2b), QColor(0xe15759), QColor(0x76b7b2),
            QColor(0x59a14f), QColor(0xedc948), QColor(0xb07aa1), QColor(0xff9da7),
            QColor(0x9c755f), QColor(0xbab0ab),
        ]

    def set_data(self, mapping: Dict[str, float]) -> None:
        items = [(k, float(v)) for k, v in mapping.items() if float(v) > 0]
        if not items:
            items = [("idle", 1.0)]
        # sort desc
        items.sort(key=lambda kv: kv[1], reverse=True)
        self._data = items
        self.update()

    # QWidget
    def minimumSizeHint(self):  # type: ignore[override]
        from PyQt6.QtCore import QSize
        return QSize(200, 150)

    def sizeHint(self):  # type: ignore[override]
        from PyQt6.QtCore import QSize
        return QSize(360, 260)

    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(10, 10, -10, -10)
        # Reserve right area for legend
        legend_w = max(140, rect.width() // 3)
        pie_rect = QRectF(rect.left(), rect.top(), rect.width() - legend_w - 10, rect.height())
        legend_rect = QRectF(pie_rect.right() + 10, rect.top(), legend_w, rect.height())

        total = sum(v for _, v in self._data) or 1.0
        start_angle = 90.0  # start at 12 o'clock, Qt uses degrees counter-clockwise

        # Draw slices
        for idx, (label, value) in enumerate(self._data):
            span = 360.0 * (value / total)
            color = self._colors[idx % len(self._colors)]
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPie(pie_rect, int(-start_angle * 16), int(-span * 16))
            start_angle += span

        # Legend
        painter.setPen(QPen(Qt.GlobalColor.black))
        font = painter.font()
        font.setPointSize(max(8, font.pointSize()))
        painter.setFont(font)

        y = legend_rect.top() + 6
        box = 10
        gap = 6
        for idx, (label, value) in enumerate(self._data):
            color = self._colors[idx % len(self._colors)]
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(int(legend_rect.left()), int(y), box, box)
            painter.setPen(QPen(Qt.GlobalColor.black))
            painter.drawText(int(legend_rect.left() + box + gap), int(y + box), f"{label} ({int(value)} MiB)")
            y += box + gap


"""A scrubbable timeline of all recorded steps, with marks for problems."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from .theme import current_theme


class Timeline(QWidget):
    seek = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total = 0
        self.pos = 0
        self.marks: list[tuple[int, str, str]] = []  # (step, severity, message)
        self.breaks: list[int] = []
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Drag to move through the recorded steps")

    def sizeHint(self) -> QSize:
        return QSize(400, 28)

    def set_trace(self, total: int, marks: list[tuple[int, str, str]], breaks: list[int]) -> None:
        self.total = total
        self.marks = marks
        self.breaks = breaks
        self.update()

    def set_position(self, pos: int) -> None:
        self.pos = pos
        self.update()

    def _track(self) -> QRectF:
        return QRectF(10, self.height() / 2 - 3, self.width() - 20, 6)

    def _x(self, step: int) -> float:
        tr = self._track()
        if self.total <= 1:
            return tr.left()
        return tr.left() + tr.width() * step / (self.total - 1)

    def _step_at(self, x: float) -> int:
        tr = self._track()
        if self.total <= 1:
            return 0
        f = (x - tr.left()) / max(1.0, tr.width())
        return max(0, min(self.total - 1, round(f * (self.total - 1))))

    def paintEvent(self, event) -> None:
        t = current_theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tr = self._track()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.box_border)
        p.drawRoundedRect(tr, 3, 3)
        if self.total == 0:
            p.end()
            return
        x = self._x(self.pos)
        done = QRectF(tr.left(), tr.top(), x - tr.left(), tr.height())
        p.setBrush(t.arrow)
        p.drawRoundedRect(done, 3, 3)
        for s in self.breaks:
            p.setPen(QPen(t.breakpoint, 1.0))
            xs = self._x(s)
            p.drawLine(QPointF(xs, tr.top() - 5), QPointF(xs, tr.top()))
        for s, sev, _ in self.marks:
            color = t.problem_border if sev == "error" else QColor("#e8a200")
            xs = self._x(s)
            p.setPen(QPen(color, 2.0))
            p.drawLine(QPointF(xs, tr.top() - 6), QPointF(xs, tr.bottom() + 6))
        p.setPen(QPen(t.frame_top_border, 2.0))
        p.setBrush(t.canvas)
        p.drawEllipse(QPointF(x, tr.center().y()), 7, 7)
        p.end()

    def mousePressEvent(self, ev) -> None:
        if self.total and ev.button() == Qt.MouseButton.LeftButton:
            self.seek.emit(self._step_at(ev.position().x()))

    def mouseMoveEvent(self, ev) -> None:
        if not self.total:
            return
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self.seek.emit(self._step_at(ev.position().x()))
            return
        x = ev.position().x()
        for s, sev, msg in self.marks:
            if abs(self._x(s) - x) < 4:
                QToolTip.showText(ev.globalPosition().toPoint(), f"step {s}: {msg}", self)
                return
        QToolTip.showText(ev.globalPosition().toPoint(), f"step {self._step_at(x)}", self)

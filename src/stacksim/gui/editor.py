"""The code editor: line numbers, breakpoints, syntax highlighting and
markers for the line that runs next, the line that just ran, and problems."""

from __future__ import annotations

import re

from PySide6.QtCore import QPointF, QRect, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPolygonF,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QToolTip, QWidget

from .theme import Theme, current_theme, mono_font

KEYWORDS = (
    "break case const continue default do else enum extern for goto if inline register restrict return "
    "sizeof static struct switch typedef union volatile while true false NULL"
).split()
TYPES = (
    "void char short int long float double signed unsigned bool _Bool size_t FILE ptrdiff_t time_t "
    "int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t intptr_t uintptr_t"
).split()


class CHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, theme: Theme):
        super().__init__(doc)
        self.set_theme(theme)

    def set_theme(self, theme: Theme) -> None:
        def fmt(color: QColor, bold=False, italic=False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(color)
            if bold:
                f.setFontWeight(700)
            if italic:
                f.setFontItalic(True)
            return f

        self.rules = [
            (QRegularExpression(r"\b[A-Za-z_]\w*(?=\s*\()"), fmt(theme.syn_function)),
            (QRegularExpression(r"\b(" + "|".join(KEYWORDS) + r")\b"), fmt(theme.syn_keyword, bold=True)),
            (QRegularExpression(r"\b(" + "|".join(TYPES) + r")\b"), fmt(theme.syn_type, bold=True)),
            (QRegularExpression(r"\b(0[xX][0-9a-fA-F]+|\d+\.?\d*([eE][+-]?\d+)?[fFuUlL]*|\.\d+)\b"), fmt(theme.syn_number)),
            (QRegularExpression(r"'(\\.|[^'\\])*'"), fmt(theme.syn_string)),
            (QRegularExpression(r'"(\\.|[^"\\])*"'), fmt(theme.syn_string)),
            (QRegularExpression(r"^\s*#\s*\w+.*$"), fmt(theme.syn_preproc)),
            (QRegularExpression(r"//[^\n]*"), fmt(theme.syn_comment, italic=True)),
        ]
        self.comment_fmt = fmt(theme.syn_comment, italic=True)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)
        # /* ... */ comments, possibly spanning lines
        self.setCurrentBlockState(0)
        i = 0
        if self.previousBlockState() == 1:
            end = text.find("*/")
            if end < 0:
                self.setFormat(0, len(text), self.comment_fmt)
                self.setCurrentBlockState(1)
                return
            self.setFormat(0, end + 2, self.comment_fmt)
            i = end + 2
        while True:
            start = text.find("/*", i)
            if start < 0:
                break
            end = text.find("*/", start + 2)
            if end < 0:
                self.setFormat(start, len(text) - start, self.comment_fmt)
                self.setCurrentBlockState(1)
                break
            self.setFormat(start, end - start + 2, self.comment_fmt)
            i = end + 2


class _Gutter(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_gutter(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            line = self.editor.line_at_y(event.position().y())
            if line is not None:
                self.editor.toggle_breakpoint(line)

    def mouseMoveEvent(self, event) -> None:
        line = self.editor.line_at_y(event.position().y())
        tip = self.editor.diag_tooltip(line) if line is not None else ""
        if tip:
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()


class CodeEditor(QPlainTextEdit):
    breakpointsChanged = Signal(set)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = current_theme()
        self.setFont(mono_font(11))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.gutter = _Gutter(self)
        self.highlighter = CHighlighter(self.document(), self.theme)
        self.breakpoints: set[int] = set()
        self.next_line: int | None = None
        self.prev_line: int | None = None
        self.error_line: bool = False
        self.hover_line: int | None = None  # a line pointed at from the memory view
        self.diags: dict[int, list] = {}  # line -> [(severity, message, col, length)]
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._refresh_selections)
        self.setMouseTracking(True)
        self._update_gutter_width()
        self._refresh_selections()

    # ------------------------------------------------------------- gutter

    def gutter_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 34 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def line_at_y(self, y: float) -> int | None:
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        while block.isValid():
            bottom = top + self.blockBoundingRect(block).height()
            if top <= y < bottom:
                return block.blockNumber() + 1
            block = block.next()
            top = bottom
        return None

    def paint_gutter(self, event) -> None:
        t = self.theme
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), t.gutter)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        fm = self.fontMetrics()
        w = self.gutter.width()
        lh = fm.height()
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + self.blockBoundingRect(block).height()
            line = block.blockNumber() + 1
            if block.isVisible() and bottom >= event.rect().top():
                cy = top + lh / 2
                if line in self.breakpoints:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(t.breakpoint)
                    p.drawEllipse(4, int(cy - 5), 10, 10)
                sev = self._line_severity(line)
                if sev and line not in self.breakpoints:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(t.problem_border if sev == "error" else QColor("#e0a800"))
                    if sev == "error":
                        p.drawRect(5, int(cy - 4), 8, 8)
                    else:
                        p.drawPolygon(QPolygonF([QPointF(9, cy - 5), QPointF(14, cy + 4), QPointF(4, cy + 4)]))
                if line == self.next_line or line == self.prev_line:
                    is_next = line == self.next_line
                    color = (t.problem_border if self.error_line else QColor("#2e9d3a")) if is_next else t.gutter_text
                    path = QPainterPath()
                    x0 = w - 18
                    path.moveTo(x0, cy - 3)
                    path.lineTo(x0 + 7, cy - 3)
                    path.lineTo(x0 + 7, cy - 7)
                    path.lineTo(x0 + 14, cy)
                    path.lineTo(x0 + 7, cy + 7)
                    path.lineTo(x0 + 7, cy + 3)
                    path.lineTo(x0, cy + 3)
                    path.closeSubpath()
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(color)
                    p.drawPath(path)
                p.setPen(t.gutter_text if line != self.next_line else t.text)
                p.setFont(self.font())
                p.drawText(QRect(16, int(top), w - 38, lh), Qt.AlignmentFlag.AlignRight, str(line))
            block = block.next()
            top = bottom
        p.end()

    def toggle_breakpoint(self, line: int) -> None:
        if line in self.breakpoints:
            self.breakpoints.discard(line)
        else:
            self.breakpoints.add(line)
        self.gutter.update()
        self.breakpointsChanged.emit(set(self.breakpoints))

    # ------------------------------------------------------------ markers

    def set_exec_lines(self, next_line: int | None, prev_line: int | None, error: bool = False, scroll: bool = True) -> None:
        self.next_line = next_line
        self.prev_line = prev_line if prev_line != next_line else None
        self.error_line = error
        self._refresh_selections()
        self.gutter.update()
        if scroll and next_line:
            block = self.document().findBlockByNumber(next_line - 1)
            if block.isValid():
                cur = QTextCursor(block)
                vp_top = self.cursorRect(cur).top()
                if vp_top < 0 or vp_top > self.viewport().height() - 2 * self.fontMetrics().height():
                    self.centerCursorOn(block)

    def centerCursorOn(self, block) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(max(0, block.blockNumber() - self.viewport().height() // max(1, self.fontMetrics().height()) // 2))

    def set_hover_line(self, line: int | None) -> None:
        if line != self.hover_line:
            self.hover_line = line
            self._refresh_selections()

    def set_diagnostics(self, diags: list) -> None:
        """diags: list of (line, col, length, severity, message)."""
        self.diags = {}
        for line, col, length, sev, msg in diags:
            if line:
                self.diags.setdefault(line, []).append((sev, msg, col, length))
        self._refresh_selections()
        self.gutter.update()

    def _line_severity(self, line: int) -> str | None:
        items = self.diags.get(line)
        if not items:
            return None
        return "error" if any(s == "error" for s, *_ in items) else "warning"

    def diag_tooltip(self, line: int | None) -> str:
        if line is None or line not in self.diags:
            return ""
        return "\n".join(f"{sev}: {msg}" for sev, msg, *_ in self.diags[line])

    def _line_selection(self, line: int, color: QColor) -> QTextEdit.ExtraSelection:
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(color)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cur = QTextCursor(self.document().findBlockByNumber(line - 1))
        sel.cursor = cur
        return sel

    def _refresh_selections(self) -> None:
        t = self.theme
        sels = []
        if self.prev_line:
            sels.append(self._line_selection(self.prev_line, t.prev_line))
        if self.next_line:
            sels.append(self._line_selection(self.next_line, t.error_line if self.error_line else t.next_line))
        if self.hover_line:
            color = QColor(t.arrow)
            color.setAlpha(70)
            sels.append(self._line_selection(self.hover_line, color))
        for line, items in self.diags.items():
            for sev, msg, col, length in items:
                block = self.document().findBlockByNumber(line - 1)
                if not block.isValid():
                    continue
                sel = QTextEdit.ExtraSelection()
                sel.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                sel.format.setUnderlineColor(t.problem_border if sev == "error" else QColor("#e0a800"))
                cur = QTextCursor(block)
                text = block.text()
                if col and col > 0:
                    start = min(col - 1, max(0, len(text) - 1))
                    n = max(1, length or 1)
                else:
                    stripped = len(text) - len(text.lstrip())
                    start, n = stripped, max(1, len(text.rstrip()) - stripped)
                cur.setPosition(block.position() + start)
                cur.setPosition(block.position() + min(len(text), start + n), QTextCursor.MoveMode.KeepAnchor)
                sel.cursor = cur
                sels.append(sel)
        self.setExtraSelections(sels)

    def event(self, ev) -> bool:
        if ev.type() == ev.Type.ToolTip:
            cur = self.cursorForPosition(ev.pos())
            line = cur.blockNumber() + 1
            tip = self.diag_tooltip(line)
            if tip:
                QToolTip.showText(ev.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(ev)

    # -------------------------------------------------------------- typing

    def keyPressEvent(self, e: QKeyEvent) -> None:
        key = e.key()
        cur = self.textCursor()
        if key == Qt.Key.Key_Tab and not cur.hasSelection():
            cur.insertText("    ")
            return
        if key == Qt.Key.Key_Backtab:
            self._dedent_line(cur)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            text = cur.block().text()
            before = text[: cur.positionInBlock()]
            indent = re.match(r"\s*", text).group()
            extra = "    " if before.rstrip().endswith("{") else ""
            after = text[cur.positionInBlock():]
            cur.beginEditBlock()
            cur.insertText("\n" + indent + extra)
            if extra and after.strip().startswith("}"):
                pos = cur.position()
                cur.insertText("\n" + indent)
                cur.setPosition(pos)
            cur.endEditBlock()
            self.setTextCursor(cur)
            return
        if e.text() == "}":
            text = cur.block().text()
            if text.strip() == "" and len(text) >= 4 and cur.positionInBlock() == len(text):
                cur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                cur.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 4)
                cur.removeSelectedText()
        super().keyPressEvent(e)

    def _dedent_line(self, cur: QTextCursor) -> None:
        block = cur.block()
        text = block.text()
        n = len(text) - len(text.lstrip(" "))
        n = min(4, n)
        if n:
            c = QTextCursor(block)
            c.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, n)
            c.removeSelectedText()

"""The memory picture: stack frames on the left, static data and heap blocks on
the right, values as boxes, and arrows from pointers to what they point to."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from ..viewmodel import ArrayView, BlockView, Cell, FrameView, MemoryState, StructView, VarView
from .theme import Theme, current_theme, mono_font, ui_font

CELL_H = 24.0
BYTES_H = 13.0
LABEL_H = 13.0
PAD = 16.0
COL_GAP = 120.0
MAX_ROW_W = 520.0
NAME_MIN = 52.0
ADDR_W = 82.0
INNER = 8.0


class _Layout:
    """Computes draw operations, pointer sources, arrow anchors and tooltips."""

    def __init__(self, canvas: "MemoryCanvas"):
        self.c = canvas
        self.t: Theme = canvas.theme
        self.ops: list = []
        self.anchors: list[tuple[int, int, QRectF, int]] = []
        self.ptrs: list[tuple[QPointF, int, int, str]] = []
        self.tips: list[tuple[QRectF, str]] = []
        self.fm_mono = QFontMetricsF(canvas.f_mono)
        self.fm_small = QFontMetricsF(canvas.f_mono_small)
        self.fm_ui = QFontMetricsF(canvas.f_ui)
        self.fm_ui_bold = QFontMetricsF(canvas.f_ui_bold)
        self.fm_tiny = QFontMetricsF(canvas.f_tiny)
        self.fm_title = QFontMetricsF(canvas.f_title)
        self.depth = 0
        self.ghost = 0
        self.frame_rects: dict[int, QRectF] = {}

    # -------------------------------------------------------------- ops

    def rect(self, r: QRectF, fill, border, radius=3.0, width=1.0, dash=False):
        self.ops.append(("rect", QRectF(r), fill, border, radius, width, dash))

    def text(self, r: QRectF, s: str, font, color, align=Qt.AlignmentFlag.AlignCenter):
        self.ops.append(("text", QRectF(r), s, font, color, align))

    def hatch(self, r: QRectF, color):
        self.ops.append(("hatch", QRectF(r), color))

    def tip(self, r: QRectF, s: str):
        if s:
            self.tips.append((QRectF(r), s))

    def anchor(self, addr: int, size: int, r: QRectF):
        self.anchors.append((addr, size, QRectF(r), self.depth))

    # ------------------------------------------------------------ values

    def cell_size(self, cell: Cell) -> tuple[float, float]:
        c = self.c
        if cell.kind == "ptr":
            if c.show_addresses and cell.init:
                w = 20 + self.fm_small.horizontalAdvance(cell.text) + 8
            else:
                w = 30
        elif cell.kind == "null":
            w = self.fm_small.horizontalAdvance("NULL") + 16
        elif cell.kind == "fn":
            w = self.fm_small.horizontalAdvance(cell.text) + 16
        else:
            text = cell.text if cell.init or cell.kind == "raw" else "?"
            w = self.fm_mono.horizontalAdvance(text) + 14
        w = max(w, 30.0)
        h = CELL_H
        if c.show_bytes and cell.kind != "raw":
            w = max(w, self.fm_tiny.horizontalAdvance(self.bytes_text(cell)) + 8)
            h += BYTES_H
        return w, h

    @staticmethod
    def bytes_text(cell: Cell) -> str:
        return " ".join(f"{b:02x}" for b in cell.raw)

    def draw_cell(self, cell: Cell, r: QRectF) -> None:
        t = self.t
        c = self.c
        fill, border, color = t.box_fill, t.box_border, t.text
        if not cell.init:
            fill, color = t.uninit_fill, t.uninit_text
        if cell.changed:
            fill, border = t.changed_fill, t.changed_border
        width = 1.0
        if cell.problem:
            border = t.problem_border
            width = 2.0
            if not cell.changed:
                fill = t.problem_fill
        self.rect(r, fill, border, 3, width)
        vr = QRectF(r.left(), r.top(), r.width(), CELL_H)
        if cell.kind == "ptr":
            dot = QPointF(r.right() - 9, vr.center().y())
            bad = cell.target_status in ("dangling", "wild", "uninit")
            ghost = self.ghost > 0
            if cell.init:
                dot_color = t.freed_border if ghost else (t.arrow_bad if bad else t.arrow)
                self.ops.append(("dot", dot, dot_color))
                if c.show_addresses:
                    self.text(QRectF(r.left() + 5, vr.top(), r.width() - 20, CELL_H), cell.text, c.f_mono_small, t.arrow_bad if bad and not ghost else t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                if cell.target is not None:
                    self.ptrs.append((dot, cell.target, cell.target_size, "ghost" if ghost else cell.target_status))
            else:
                self.text(vr, "?", c.f_mono, color)
        elif cell.kind == "null":
            self.text(vr, "NULL", c.f_mono_small, t.null_text)
        elif cell.kind == "fn":
            self.text(vr, cell.text, c.f_mono_small, t.syn_function)
        else:
            text = cell.text if (cell.init or cell.kind == "raw") else "?"
            self.text(vr, text, c.f_mono, color)
        if c.show_bytes and cell.kind != "raw":
            self.text(QRectF(r.left(), r.top() + CELL_H - 2, r.width(), BYTES_H), self.bytes_text(cell), c.f_tiny, t.address)
        tip = cell.tooltip
        if cell.label and not cell.label.startswith("+"):
            tip = f"{cell.label}\n{tip}"
        self.tip(r, tip)
        self.anchor(cell.addr, cell.size, r)

    def place(self, v, x: float, y: float, emit: bool) -> tuple[float, float]:
        if isinstance(v, Cell):
            w, h = self.cell_size(v)
            if emit:
                self.draw_cell(v, QRectF(x, y, w, h))
            return w, h
        if isinstance(v, ArrayView):
            return self.place_array(v, x, y, emit)
        if isinstance(v, StructView):
            return self.place_struct(v, x, y, emit)
        return 0.0, 0.0

    def place_array(self, a: ArrayView, x: float, y: float, emit: bool) -> tuple[float, float]:
        t = self.t
        c = self.c
        elems = a.elems
        if not elems:
            if emit:
                self.text(QRectF(x, y, 60, CELL_H), "(empty)", c.f_tiny, t.text_dim)
            return 60.0, CELL_H
        if all(isinstance(e, Cell) for e in elems):
            sizes = [self.cell_size(e) for e in elems]
            cw = max(s[0] for s in sizes)
            ch = max(s[1] for s in sizes)
            raw = elems[0].kind == "raw"
            slots: list = list(elems)
            if a.hidden:
                slots.insert(len(slots) - 4 if len(slots) > 4 else len(slots), None)
            per_row = max(1, min(len(slots), int((MAX_ROW_W + 2) // (cw + 2))))
            if raw:
                per_row = min(per_row, 16)
            rows = math.ceil(len(slots) / per_row)
            row_h = LABEL_H + ch + 3
            for i, e in enumerate(slots):
                col, row = i % per_row, i // per_row
                cx = x + col * (cw + 2)
                cy = y + row * row_h
                if not emit:
                    continue
                if e is None:
                    self.text(QRectF(cx, cy + LABEL_H, cw, ch), "...", c.f_mono, t.text_dim)
                    self.tip(QRectF(cx, cy + LABEL_H, cw, ch), f"{a.hidden} more elements are not shown")
                    continue
                label = e.label.strip("[]") if not raw else (e.label if i % 4 == 0 else "")
                self.text(QRectF(cx, cy, cw, LABEL_H), label, c.f_tiny, t.address)
                self.draw_cell(e, QRectF(cx, cy + LABEL_H, cw, ch))
            w = min(len(slots), per_row) * (cw + 2) - 2
            h = rows * row_h - 3
            if a.text is not None:
                if emit:
                    shown = a.text if len(a.text) <= 40 else a.text[:37] + "..."
                    s = '"' + shown.replace("\n", "\\n").replace("\t", "\\t") + '"'
                    self.text(QRectF(x, y + h + 1, max(w, 200), 14), s, c.f_mono_small, t.syn_string, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                h += 15
            if emit:
                self.anchor(a.addr, a.size, QRectF(x, y + LABEL_H, w, ch))
            return w, h
        # Arrays of structs/arrays: one element per line.
        label_w = max(self.fm_tiny.horizontalAdvance(e.label) for e in elems if not isinstance(e, Cell)) + 6
        cy = y
        w = 0.0
        for e in elems:
            ew, eh = self.place(e, x + label_w, cy, False)
            if emit:
                self.text(QRectF(x, cy, label_w - 4, min(eh, CELL_H + LABEL_H)), e.label, c.f_tiny, t.address, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.place(e, x + label_w, cy, True)
            w = max(w, label_w + ew)
            cy += eh + 4
        if a.hidden and emit:
            self.text(QRectF(x, cy, 200, 14), f"... {a.hidden} more elements", c.f_tiny, t.text_dim, Qt.AlignmentFlag.AlignLeft)
        if a.hidden:
            cy += 16
        if emit:
            self.anchor(a.addr, a.size, QRectF(x, y, w, cy - y - 4))
        return w, cy - y - 4

    def place_struct(self, s: StructView, x: float, y: float, emit: bool) -> tuple[float, float]:
        t = self.t
        c = self.c
        name_w = max([self.fm_small.horizontalAdvance(n) for n, _ in s.fields] + [10]) + 8
        cy = y + 4
        w = 0.0
        sizes = []
        for name, v in s.fields:
            fw, fh = self.place(v, 0, 0, False)
            sizes.append((fw, fh))
            w = max(w, name_w + fw)
        bw = w + 12
        bh = sum(max(h, CELL_H) + 3 for _, h in sizes) + 5
        if emit:
            r = QRectF(x, y, bw, bh)
            self.rect(r, None, t.box_border, 4, 1.0, dash=False)
            self.depth += 1
            for (name, v), (fw, fh) in zip(s.fields, sizes):
                label_y = cy + (LABEL_H if isinstance(v, ArrayView) and all(isinstance(e, Cell) for e in v.elems) else 0)
                self.text(QRectF(x + 6, label_y, name_w - 4, CELL_H), name, c.f_mono_small, t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.place(v, x + 6 + name_w, cy, True)
                cy += max(fh, CELL_H) + 3
            self.depth -= 1
            self.anchor(s.addr, s.size, r)
            self.tip(QRectF(x, y, bw, 4), s.type_name)
        return bw, bh

    # -------------------------------------------------------------- rows

    def name_width(self, rows: list[VarView]) -> float:
        w = NAME_MIN
        for v in rows:
            w = max(w, self.fm_ui_bold.horizontalAdvance(v.name) + 6, self.fm_tiny.horizontalAdvance(v.type_name) + 6)
        return min(w, 220.0)

    def var_row(self, v: VarView, x: float, y: float, name_w: float, emit: bool, gutter_x: float | None = None) -> tuple[float, float]:
        t = self.t
        c = self.c
        vw, vh = self.place(v.value, 0, 0, False)
        h = max(34.0, vh + 8)
        w = name_w + 8 + vw
        addr_text = f"0x{v.addr:x}"
        inline_addr = c.show_addresses and gutter_x is None
        if inline_addr:
            w += 10 + self.fm_small.horizontalAdvance(addr_text)
        if emit:
            if v.problem:
                self.rect(QRectF(x - 4, y + 1, w + 8, h - 2), t.problem_fill, t.problem_border, 4, 1.5)
            name = v.name if self.fm_ui_bold.horizontalAdvance(v.name) <= name_w else v.name[: max(3, int(name_w / 8))] + "..."
            self.text(QRectF(x, y + 3, name_w, 16), name, c.f_ui_bold, t.text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.text(QRectF(x, y + 18, name_w, 13), v.type_name, c.f_tiny, t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.tip(
                QRectF(x, y, name_w, h),
                f"{v.name}: {v.type_name}\n{v.size} byte{'s' if v.size != 1 else ''} at 0x{v.addr:x}" + (f"\n{v.note}" if v.note else ""),
            )
            vx, vy = x + name_w + 8, y + 4
            self.depth += 1
            self.place(v.value, vx, vy, True)
            self.depth -= 1
            self.anchor(v.addr, v.size, QRectF(vx, vy, vw, vh))
            ay = vy + (LABEL_H if self._has_labels(v.value) else 0)
            if inline_addr:
                self.text(QRectF(vx + vw + 10, ay, 120, CELL_H), addr_text, c.f_mono_small, t.address, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            elif c.show_addresses:
                self.text(QRectF(gutter_x, ay, ADDR_W - 8, CELL_H), addr_text, c.f_mono_small, t.address, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return w, h

    @staticmethod
    def _has_labels(v) -> bool:
        return isinstance(v, ArrayView) and bool(v.elems) and all(isinstance(e, Cell) for e in v.elems)

    # ------------------------------------------------------------ cards

    def frame_card(self, f: FrameView, x: float, y: float, width: float | None, emit: bool) -> tuple[float, float]:
        t = self.t
        c = self.c
        name_w = self.name_width(f.vars)
        head_h = 24.0
        title = f"{f.func}()"
        right = (f"line {f.line}" if f.line else "") if not f.is_top else (f"next: line {f.line}" if f.line else "")
        w = self.fm_title.horizontalAdvance(title) + self.fm_ui.horizontalAdvance(right) + 40
        rows = []
        for v in f.vars:
            rw, rh = self.var_row(v, 0, 0, name_w, False)
            rows.append((rw, rh))
            w = max(w, rw + 2 * INNER)
        if not f.vars:
            w = max(w, 160)
        foot_h = 22.0 if f.ret_text is not None else 0.0
        body_h = sum(rh for _, rh in rows) if rows else 22.0
        h = head_h + body_h + 6 + foot_h
        if width is not None:
            w = max(w, width)
        if not emit:
            return w, h
        border = t.frame_top_border if f.is_top else t.frame_border
        bw = 2.0 if f.is_top else 1.0
        r = QRectF(x, y, w, h)
        self.frame_rects[f.id] = QRectF(r)
        self.rect(r, t.frame_fill, border, 6, bw)
        hr = QRectF(x, y, w, head_h)
        self.ops.append(("head", hr, t.frame_header, border, bw))
        self.text(QRectF(x + INNER, y, w - 2 * INNER, head_h), title, c.f_title, t.text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.text(QRectF(x + INNER, y, w - 2 * INNER, head_h), right, c.f_ui, t.frame_top_border if f.is_top else t.text_dim, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tip = f"Stack frame of {f.func}()"
        if not f.is_top:
            tip += f"\nwaiting at line {f.line} for the function it called to return"
        if c.show_addresses:
            tip += f"\nframe starts below 0x{f.base:x}"
        self.tip(hr, tip)
        if f.header_problem:
            self.rect(QRectF(x + 2, y + head_h - 4, w - 4, 4), t.problem_border, None, 1)
            self.tip(QRectF(x, y + head_h - 6, w, 8), "The return address of this function (stored at the top of its frame) was overwritten!")
        cy = y + head_h + 3
        if not f.vars:
            self.text(QRectF(x + INNER, cy, w - 2 * INNER, 20), "(no local variables)", c.f_tiny, t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.depth += 1
        for v, (_, rh) in zip(f.vars, rows):
            self.var_row(v, x + INNER, cy, name_w, True, gutter_x=x - ADDR_W)
            cy += rh
        self.depth -= 1
        if f.ret_text is not None:
            fr = QRectF(x + INNER, y + h - foot_h - 2, w - 2 * INNER, foot_h)
            self.text(fr, f"returns {f.ret_text}", c.f_ui_bold, t.frame_top_border, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return w, h

    def block_card(self, b: BlockView, x: float, y: float, width: float | None, emit: bool) -> tuple[float, float]:
        t = self.t
        c = self.c
        head_h = 24.0
        title = b.label
        if b.status == "freed":
            info = f"{b.size} B - freed at line {b.free_line}"
        else:
            info = f"{b.size} B - malloc'd at line {b.alloc_line}" if b.alloc_line else f"{b.size} B"
        if c.show_addresses:
            info += f" - 0x{b.addr:x}"
        type_w = self.fm_tiny.horizontalAdvance(b.type_name) + 8
        vw, vh = self.place(b.value, 0, 0, False)
        w = max(self.fm_title.horizontalAdvance(title) + self.fm_ui.horizontalAdvance(info) + 40, vw + 2 * INNER, type_w + 2 * INNER)
        banner_h = 18.0 if b.status in ("leaked", "freed") else 0.0
        h = head_h + banner_h + 14 + vh + 10
        if width is not None:
            w = max(w, width)
        if not emit:
            return w, h
        if b.status == "freed":
            fill, border, head = t.freed_fill, t.freed_border, t.freed_fill
        elif b.status == "leaked":
            fill, border, head = t.leak_fill, t.leak_border, t.leak_fill
        else:
            fill, border, head = t.heap_fill, t.heap_border, t.heap_header
        bw = 2.0 if (b.status == "leaked" or b.problem) else 1.0
        if b.problem:
            border = t.problem_border
        r = QRectF(x, y, w, h)
        self.rect(r, fill, border, 6, bw, dash=b.status == "freed")
        if b.status == "freed":
            self.hatch(r, t.freed_border)
        self.ops.append(("head", QRectF(x, y, w, head_h), head, border, bw))
        dim = b.status == "freed"
        self.text(QRectF(x + INNER, y, w - 2 * INNER, head_h), title, c.f_title, t.text_dim if dim else t.text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.text(QRectF(x + INNER, y, w - 2 * INNER, head_h), info, c.f_ui, t.text_dim, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        cy = y + head_h
        if b.status == "leaked":
            self.text(QRectF(x + INNER, cy, w - 2 * INNER, banner_h), "LEAKED: no pointer leads to this block anymore", c.f_ui_bold, t.leak_border, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        elif b.status == "freed":
            self.text(QRectF(x + INNER, cy, w - 2 * INNER, banner_h), "FREED: this memory no longer belongs to the program", c.f_ui_bold, t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cy += banner_h
        self.text(QRectF(x + INNER, cy, w - 2 * INNER, 13), b.type_name, c.f_tiny, t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        tip = f"{b.label}: {b.size} bytes on the heap at 0x{b.addr:x}"
        if b.alloc_line:
            tip += f"\nallocated at line {b.alloc_line}"
        if b.status == "freed":
            tip += f"\nfreed at line {b.free_line} - pointers to it are dangling"
        elif b.status == "leaked":
            tip += "\nNo variable or other block points here anymore. The block can never be freed: this is a memory leak."
        tip += f"\nmalloc reserved a {b.chunk}-byte chunk for it (16-byte bookkeeping header + rounded-up size)" if b.chunk else ""
        self.tip(QRectF(x, y, w, head_h + banner_h), tip)
        self.depth += 1
        if dim:
            self.ghost += 1
        self.place(b.value, x + INNER, cy + 14, True)
        if dim:
            self.ghost -= 1
        self.depth -= 1
        self.anchor(b.addr, max(1, b.size), QRectF(x + INNER, cy + 14, vw, vh))
        return w, h

    def static_card(self, rows: list[VarView], x: float, y: float, width: float | None, emit: bool) -> tuple[float, float]:
        t = self.t
        name_w = self.name_width(rows)
        sizes = [self.var_row(v, 0, 0, name_w, False) for v in rows]
        w = max([rw + 2 * INNER for rw, _ in sizes] + [180.0])
        h = sum(rh for _, rh in sizes) + 10
        if width is not None:
            w = max(w, width)
        if not emit:
            return w, h
        self.rect(QRectF(x, y, w, h), t.static_fill, t.static_border, 6, 1.0)
        cy = y + 5
        self.depth += 1
        for v, (_, rh) in zip(rows, sizes):
            self.var_row(v, x + INNER, cy, name_w, True)
            cy += rh
        self.depth -= 1
        return w, h

    def column_title(self, x: float, y: float, title: str, subtitle: str) -> float:
        c = self.c
        self.text(QRectF(x, y, 400, 20), title, c.f_section, self.t.text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        tw = QFontMetricsF(c.f_section).horizontalAdvance(title)
        self.text(QRectF(x + tw + 10, y + 1, 420, 20), subtitle, c.f_ui, self.t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return 26.0

    # ------------------------------------------------------------- whole

    def build(self, s: MemoryState) -> QSize:
        t = self.t
        c = self.c
        y0 = PAD
        y0 += self.legend(PAD, y0)
        # ---- left column: the stack (addresses in a gutter on its left)
        x = PAD + (ADDR_W if c.show_addresses else 0)
        frame_sizes = [self.frame_card(f, 0, 0, None, False) for f in s.frames]
        left_w = max([w for w, _ in frame_sizes] + [300.0])
        y = y0
        used = f"{s.stack_bytes} bytes in use" if s.frames else "empty"
        y += self.column_title(PAD, y, "Stack", f"newest call at the bottom - {used}")
        if not s.frames:
            self.text(QRectF(x, y, left_w, 40), "(no function is running)", c.f_ui, t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            y += 40
        for i, (f, (fw, fh)) in enumerate(zip(s.frames, frame_sizes)):
            if s.frames_hidden and i == 3:
                r = QRectF(x, y, left_w, 30)
                self.rect(r, None, t.frame_border, 6, 1.0, dash=True)
                self.text(r, f"... {s.frames_hidden} more stack frames ...", c.f_ui, t.text_dim)
                y += 38
            self.frame_card(f, x, y, left_w, True)
            y += fh + 10
        left_h = y
        # ---- right column: static data, then the heap
        x2 = x + left_w + COL_GAP
        statics = s.statics
        block_sizes = [self.block_card(b, 0, 0, None, False) for b in s.blocks]
        static_size = self.static_card(statics, 0, 0, None, False) if statics else (0.0, 0.0)
        right_w = max([w for w, _ in block_sizes] + [static_size[0], 300.0])
        y = y0
        if statics:
            y += self.column_title(x2, y, "Static data", "global variables, static variables, string literals")
            self.static_card(statics, x2, y, right_w, True)
            y += static_size[1] + 18
        live = [b for b in s.blocks if b.status != "freed"]
        heap_sub = f"{len(live)} block{'s' if len(live) != 1 else ''}, {s.heap_live_bytes} bytes in use" if live else "nothing allocated"
        if s.leaked_bytes:
            heap_sub += f" - {s.leaked_bytes} bytes leaked"
        y += self.column_title(x2, y, "Heap", heap_sub)
        if not s.blocks:
            self.text(QRectF(x2, y, right_w, 40), "(empty - memory from malloc() appears here)", c.f_ui, t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            y += 40
        for b, (bw, bh) in zip(s.blocks, block_sizes):
            self.block_card(b, x2, y, right_w, True)
            y += bh + 10
        right_h = y
        width = x2 + right_w + PAD + 60
        height = max(left_h, right_h) + PAD
        return QSize(int(width), int(height))

    def legend(self, x: float, y: float) -> float:
        t = self.t
        c = self.c
        items = [
            (t.changed_fill, t.changed_border, "changed in this step"),
            (t.uninit_fill, t.box_border, "? = never given a value (garbage)"),
            (t.problem_fill, t.problem_border, "problem in this step"),
            (t.leak_fill, t.leak_border, "leaked heap block"),
        ]
        cx = x
        for fill, border, label in items:
            self.rect(QRectF(cx, y + 3, 12, 12), fill, border, 2, 1.0)
            lw = self.fm_tiny.horizontalAdvance(label)
            self.text(QRectF(cx + 16, y, lw + 4, 18), label, c.f_tiny, t.text_dim, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            cx += 16 + lw + 18
        return 26.0

    # ------------------------------------------------------------ arrows

    def resolve(self, target: int, tsize: int, status: str) -> tuple[QRectF | None, bool]:
        best = None
        for addr, size, r, depth in self.anchors:
            if addr <= target < addr + max(size, 1):
                key = (0 if size >= tsize else 1, size, -depth)
                if best is None or key < best[0]:
                    best = (key, r)
        if best is not None:
            return best[1], False
        if status == "end":
            for addr, size, r, depth in self.anchors:
                if addr + size == target:
                    return r, True
        return None, False


class MemoryCanvas(QWidget):
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = current_theme()
        self.state: MemoryState | None = None
        self.zoom = 1.0
        self.show_addresses = True
        self.show_bytes = False
        self.stale = False
        self.placeholder = "Press Run (F5) to start the program.\nThe stack and the heap will appear here."
        self.f_mono = mono_font(9.5)
        self.f_mono_small = mono_font(8.0)
        self.f_tiny = ui_font(7.5)
        self.f_ui = ui_font(8.5)
        self.f_ui_bold = ui_font(9.0, bold=True)
        self.f_title = ui_font(9.5, bold=True)
        self.f_section = ui_font(13, bold=True)
        self.layout_: _Layout | None = None
        self.content = QSize(400, 300)
        self.setMouseTracking(True)
        self.hover_ptr: int | None = None

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.relayout()

    def set_state(self, state: MemoryState | None) -> None:
        self.state = state
        self.relayout()

    def set_option(self, name: str, value) -> None:
        setattr(self, name, value)
        self.relayout()

    def set_zoom(self, z: float) -> None:
        self.zoom = max(0.4, min(2.5, z))
        self.relayout()
        self.zoomChanged.emit(self.zoom)

    def relayout(self) -> None:
        if self.state is None:
            self.layout_ = None
            self.content = QSize(400, 200)
        else:
            lay = _Layout(self)
            self.content = lay.build(self.state)
            self.layout_ = lay
        size = QSize(int(self.content.width() * self.zoom), int(self.content.height() * self.zoom))
        self.setMinimumSize(size)
        self.resize(max(size.width(), self.parentWidget().width() if self.parentWidget() else 0), max(size.height(), self.parentWidget().height() if self.parentWidget() else 0))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(int(self.content.width() * self.zoom), int(self.content.height() * self.zoom))

    # ----------------------------------------------------------- painting

    def paintEvent(self, event) -> None:
        t = self.theme
        p = QPainter(self)
        p.fillRect(self.rect(), t.canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.layout_ is None:
            p.setPen(t.text_dim)
            p.setFont(ui_font(11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)
            p.end()
            return
        p.scale(self.zoom, self.zoom)
        for op in self.layout_.ops:
            kind = op[0]
            if kind == "rect":
                _, r, fill, border, radius, width, dash = op
                p.setBrush(QBrush(fill) if fill is not None else Qt.BrushStyle.NoBrush)
                if border is None:
                    p.setPen(Qt.PenStyle.NoPen)
                else:
                    pen = QPen(border, width)
                    if dash:
                        pen.setStyle(Qt.PenStyle.DashLine)
                    p.setPen(pen)
                p.drawRoundedRect(r, radius, radius)
            elif kind == "head":
                _, r, fill, border, width = op
                path = QPainterPath()
                rad = 6.0
                path.moveTo(r.left(), r.bottom())
                path.lineTo(r.left(), r.top() + rad)
                path.quadTo(r.left(), r.top(), r.left() + rad, r.top())
                path.lineTo(r.right() - rad, r.top())
                path.quadTo(r.right(), r.top(), r.right(), r.top() + rad)
                path.lineTo(r.right(), r.bottom())
                path.closeSubpath()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                inset = width / 2
                p.save()
                p.setClipRect(r.adjusted(inset, inset, -inset, 0))
                p.drawPath(path)
                p.restore()
                p.setPen(QPen(border, 1.0))
                p.drawLine(QPointF(r.left() + inset, r.bottom()), QPointF(r.right() - inset, r.bottom()))
            elif kind == "text":
                _, r, s, font, color, align = op
                p.setFont(font)
                p.setPen(color)
                p.drawText(r, int(align), s)
            elif kind == "dot":
                _, pt, color = op
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                p.drawEllipse(pt, 4.0, 4.0)
            elif kind == "hatch":
                _, r, color = op
                p.save()
                p.setClipRect(r)
                c = QColor(color)
                c.setAlpha(60)
                p.setPen(QPen(c, 1.0))
                step = 10.0
                xx = r.left() - r.height()
                while xx < r.right():
                    p.drawLine(QPointF(xx, r.bottom()), QPointF(xx + r.height(), r.top()))
                    xx += step
                p.restore()
        self.paint_arrows(p)
        if self.stale:
            p.resetTransform()
            p.fillRect(self.rect(), QColor(t.canvas.red(), t.canvas.green(), t.canvas.blue(), 150))
        p.end()

    def paint_arrows(self, p: QPainter) -> None:
        t = self.theme
        lay = self.layout_
        for i, (src, target, tsize, status) in enumerate(lay.ptrs):
            r, to_end = lay.resolve(target, tsize, status)
            bad = status in ("dangling", "wild")
            ghost = status == "ghost"
            color = t.freed_border if ghost else (t.arrow_bad if bad else t.arrow)
            pen = QPen(color, 2.2 if self.hover_ptr == i else (1.0 if ghost else 1.5))
            if bad or ghost:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if r is None and status == "ghost":
                continue
            if r is None:
                # Target not shown: a short stub with a cross.
                e = QPointF(src.x() + 34, src.y())
                p.drawLine(src, e)
                p.drawLine(QPointF(e.x() - 4, e.y() - 4), QPointF(e.x() + 4, e.y() + 4))
                p.drawLine(QPointF(e.x() - 4, e.y() + 4), QPointF(e.x() + 4, e.y() - 4))
                continue
            sx, sy = src.x(), src.y()
            if to_end:
                e = QPointF(r.right() + 1, r.center().y())
                xr = max(sx, e.x()) + 30
                c1, c2 = QPointF(xr, sy), QPointF(xr, e.y())
            elif r.left() > sx + 24:
                e = QPointF(r.left() - 1, min(max(sy, r.top() + 8), r.bottom() - 8) if r.height() > 40 else r.center().y())
                dx = max(30.0, (e.x() - sx) / 2)
                c1, c2 = QPointF(sx + dx, sy), QPointF(e.x() - dx, e.y())
            else:
                e = QPointF(r.right() + 1, r.center().y() if r.height() <= 40 else r.top() + 12)
                xr = max(sx, e.x()) + 40 + min(160.0, abs(e.y() - sy) / 5)
                c1, c2 = QPointF(xr, sy), QPointF(xr, e.y())
            path = QPainterPath(src)
            path.cubicTo(c1, c2, e)
            p.drawPath(path)
            # arrow head
            dx, dy = e.x() - c2.x(), e.y() - c2.y()
            ln = math.hypot(dx, dy) or 1.0
            ux, uy = dx / ln, dy / ln
            size = 8.0
            left = QPointF(e.x() - ux * size - uy * size * 0.5, e.y() - uy * size + ux * size * 0.5)
            right = QPointF(e.x() - ux * size + uy * size * 0.5, e.y() - uy * size - ux * size * 0.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawPolygon(QPolygonF([e, left, right]))

    # -------------------------------------------------------------- events

    def event(self, ev) -> bool:
        if ev.type() == ev.Type.ToolTip and self.layout_ is not None:
            pos = QPointF(ev.pos()) / self.zoom
            for r, s in reversed(self.layout_.tips):
                if r.contains(pos):
                    QToolTip.showText(ev.globalPos(), s, self)
                    return True
            QToolTip.hideText()
            return True
        return super().event(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self.layout_ is None:
            return
        pos = QPointF(ev.position()) / self.zoom
        hit = None
        for i, (src, *_rest) in enumerate(self.layout_.ptrs):
            if abs(src.x() - pos.x()) < 9 and abs(src.y() - pos.y()) < 9:
                hit = i
                break
        if hit != self.hover_ptr:
            self.hover_ptr = hit
            self.update()

    def wheelEvent(self, ev) -> None:
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom * (1.1 if ev.angleDelta().y() > 0 else 1 / 1.1))
            ev.accept()
            return
        super().wheelEvent(ev)

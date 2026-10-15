"""Builds a GUI-independent description of the memory at one step of a trace.

The GUI only has to lay out and paint what this module produces: stack frames
with their variables, heap blocks, static data, values decoded from the bytes,
pointer targets and their status (valid, dangling, NULL, garbage), which bytes
changed in the last step, and which heap blocks have become unreachable.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from .c import ctype as T
from .c.errors import Diagnostic
from .vm.memory import Memory
from .vm.trace import Cursor, FrameRec, MemObject, Trace
from .vm.values import char_repr, decode, fmt_float

MAX_ARRAY_CELLS = 64
MAX_FRAMES_SHOWN = 14
MAX_FREED_SHOWN = 8
MAX_SCAN_BYTES = 64 * 1024


@dataclass
class Cell:
    addr: int
    size: int
    text: str
    kind: str  # int float char bool ptr null fn raw pad
    init: bool
    changed: bool
    label: str = ""
    target: int | None = None
    target_status: str = ""  # ok dangling wild null uninit fn end
    target_size: int = 1  # size of the pointed-to type (to pick the best arrow target)
    always_text: bool = False  # show the pointer value even when addresses are switched off
    tooltip: str = ""
    raw: bytes = b""
    problem: bool = False


@dataclass
class ArrayView:
    addr: int
    size: int
    label: str
    elems: list
    elem_type: str
    total: int
    hidden: int = 0  # number of elements not shown
    text: str | None = None  # string preview for char arrays
    changed: bool = False


@dataclass
class StructView:
    addr: int
    size: int
    label: str
    fields: list  # (name, value)
    type_name: str
    changed: bool = False


@dataclass
class VarView:
    obj_id: int
    name: str
    type_name: str
    addr: int
    size: int
    value: object
    changed: bool
    is_new: bool
    problem: bool = False
    note: str = ""


@dataclass
class FrameSlot:
    """One of the bookkeeping slots at the top of a stack frame."""

    kind: str  # retaddr | savedfp
    cell: Cell
    description: str
    line: int | None = None  # the source line a return address leads back to
    intact: bool = True


@dataclass
class FrameView:
    id: int
    func: str
    line: int | None
    vars: list[VarView]
    is_top: bool
    ret_text: str | None
    slots: list[FrameSlot] = field(default_factory=list)  # return address, saved frame pointer
    header_problem: bool = False
    depth: int = 0
    base: int = 0


@dataclass
class BlockView:
    obj_id: int
    label: str
    addr: int
    size: int
    chunk: int
    status: str  # live leaked freed
    alloc_line: int | None
    free_line: int | None
    type_name: str
    value: object
    changed: bool
    is_new: bool
    problem: bool = False


@dataclass
class MemoryState:
    step: int
    total_steps: int
    line: int | None
    prev_line: int | None
    kind: str
    frames: list[FrameView]
    frames_hidden: int
    blocks: list[BlockView]
    statics: list[VarView]
    events: list[str]
    diagnostics: list[Diagnostic]
    sp: int
    stack_bytes: int
    heap_live_bytes: int
    heap_live_blocks: int
    leaked_bytes: int
    problem_ranges: list[tuple[int, int]] = field(default_factory=list)
    frame_notes: list[str] = field(default_factory=list)  # explanations about return addresses


def _overlaps(ranges: list[tuple[int, int]], addr: int, size: int) -> bool:
    end = addr + size
    for a, n in ranges:
        if a < end and addr < a + n:
            return True
    return False


class _ObjectIndex:
    """Lookup of the objects alive at one step, by address."""

    def __init__(self, objs: list[MemObject]):
        objs = sorted(objs, key=lambda o: o.addr)
        self.objs = objs
        self.starts = [o.addr for o in objs]

    def find(self, addr: int) -> MemObject | None:
        i = bisect.bisect_right(self.starts, addr) - 1
        while i >= 0:
            o = self.objs[i]
            if o.addr <= addr < o.addr + max(o.size, 1):
                return o
            if o.addr + 4096 < addr:
                break
            i -= 1
        return None

    def find_end(self, addr: int) -> MemObject | None:
        """An object that ends exactly at addr (pointer one past the end)."""
        i = bisect.bisect_left(self.starts, addr) - 1
        while i >= 0:
            o = self.objs[i]
            if o.addr + o.size == addr:
                return o
            if o.addr + 4096 < addr:
                break
            i -= 1
        return None


class ViewBuilder:
    def __init__(self, trace: Trace):
        self.trace = trace
        self.cursor = Cursor(trace)
        self.mem: Memory = trace.memory

    def build(self, step: int, show_freed: bool = True) -> MemoryState:
        tr = self.trace
        step = max(0, min(step, len(tr.steps) - 1))
        self.cursor.goto(step)
        st = tr.steps[step]
        self.step = step
        self.changed = [(e[0], len(e[3])) for e in st.delta if len(e) == 5]
        diags = [tr.diagnostics[i] for i in st.diags]
        self.problem_ranges = [r for d in diags for r in d.addrs]

        alive = [o for o in tr.objects if o.birth <= step and (o.death is None or step < o.death)]
        self.index = _ObjectIndex(alive)
        self.alive_ids = {o.id for o in alive}

        frames, hidden = self.build_frames(step)
        statics = self.build_statics(alive)
        heap_live = [o for o in alive if o.kind == "heap"]
        reachable = self.reachable(alive, heap_live)
        blocks = self.build_blocks(step, heap_live, reachable, show_freed)
        # String literals become visible when some pointer points at them.
        targets = set()
        for fv in frames:
            for v in fv.vars:
                self._collect_targets(v.value, targets)
        for b in blocks:
            self._collect_targets(b.value, targets)
        for v in statics:
            self._collect_targets(v.value, targets)
        literal_views = []
        for o in alive:
            if o.kind == "literal" and any(o.addr <= t <= o.addr + o.size for t in targets):
                literal_views.append(self.var_view(o, name="string literal"))
        statics.extend(literal_views)

        stack_bytes = 0
        if frames:
            stack_bytes = tr.frames[frames[0].id].base - st.sp if frames else 0
        leaked = [b for b in blocks if b.status == "leaked"]
        prev_line = tr.steps[step - 1].line if step > 0 else None
        return MemoryState(
            step=step,
            total_steps=len(tr.steps),
            line=st.line,
            prev_line=prev_line,
            kind=st.kind,
            frames=frames,
            frames_hidden=hidden,
            blocks=blocks,
            statics=statics,
            events=list(st.events),
            diagnostics=diags,
            sp=st.sp,
            stack_bytes=stack_bytes,
            heap_live_bytes=sum(o.size for o in heap_live),
            heap_live_blocks=len(heap_live),
            leaked_bytes=sum(b.size for b in leaked),
            problem_ranges=self.problem_ranges,
            frame_notes=self.frame_notes(step),
        )

    # ------------------------------------------------------------- pieces

    def build_frames(self, step: int) -> tuple[list[FrameView], int]:
        tr = self.trace
        chain: list[FrameRec] = tr.frame_chain(step)
        hidden = 0
        if len(chain) > MAX_FRAMES_SHOWN:
            keep_top = MAX_FRAMES_SHOWN - 3
            hidden = len(chain) - MAX_FRAMES_SHOWN
            shown = chain[:3] + chain[-keep_top:]
        else:
            shown = chain
        st = tr.steps[step]
        views = []
        for i, fr in enumerate(shown):
            is_top = fr is chain[-1]
            if is_top:
                line = st.line
            else:
                idx = chain.index(fr)
                line = chain[idx + 1].call_line
            vars_ = []
            for oid in fr.vars:
                o = tr.objects[oid]
                if o.hidden or not (o.birth <= step and (o.death is None or step < o.death)):
                    continue
                vars_.append(self.var_view(o))
            ret_text = fr.ret_text if (is_top and st.kind == "return") else None
            slots = self.frame_slots(fr)
            views.append(
                FrameView(
                    id=fr.id,
                    func=fr.func,
                    line=line,
                    vars=vars_,
                    is_top=is_top,
                    ret_text=ret_text,
                    slots=slots,
                    header_problem=any(sl.cell.problem or not sl.intact for sl in slots),
                    depth=fr.depth,
                    base=fr.base,
                )
            )
        return views, hidden

    def frame_slots(self, fr: FrameRec) -> list[FrameSlot]:
        """The return address and the saved frame pointer at the top of a frame."""
        tr = self.trace
        if fr.ret_obj is None or fr.fp_obj is None:
            return []
        out = []
        ro = tr.objects[fr.ret_obj]
        got = self.mem.peek(ro.addr, 8)
        if got is not None:
            data = got[0]
            v = int.from_bytes(data, "little")
            intact = v == fr.ret_addr
            target = tr.describe_code(v)
            site = tr.return_sites.get(v)
            if intact:
                desc = f"return to {target}"
            else:
                desc = "OVERWRITTEN! " + (f"now leads to {target}" if target else "this is no code address any more")
            tip = (
                f"Return address (8 bytes at 0x{ro.addr:x})\n"
                f"The call instruction stored here where the program continues when {fr.func}() returns:\n"
                f"0x{fr.ret_addr:x} = {tr.describe_code(fr.ret_addr)}."
            )
            if not intact:
                tip += f"\nIt was overwritten with 0x{v:x}: returning would make the CPU jump there."
            cell = Cell(
                ro.addr, 8, f"0x{v:x}", "code", True,
                _overlaps(self.changed, ro.addr, 8), "", tooltip=tip, raw=data,
                problem=_overlaps(self.problem_ranges, ro.addr, 8) or not intact,
            )
            out.append(FrameSlot("retaddr", cell, desc, site[1] if site and intact else None, intact))
        fo = tr.objects[fr.fp_obj]
        got = self.mem.peek(fo.addr, 8)
        if got is not None:
            data = got[0]
            v = int.from_bytes(data, "little")
            intact = v == fr.saved_fp
            parent = tr.frames[fr.parent] if fr.parent is not None else None
            if not intact:
                desc = "OVERWRITTEN! the caller's frame pointer is lost"
                kind, target, status = "ptr", v or None, "wild"
            elif parent is None:
                desc = "0: main() has no caller frame"
                kind, target, status = "code", None, ""
            else:
                desc = f"{parent.func}()'s frame pointer"
                kind, target, status = "ptr", v, "frame"
            caller_name = f"{parent.func}()" if parent else "its caller"
            tip = (
                f"Saved frame pointer (8 bytes at 0x{fo.addr:x})\n"
                f"{fr.func}() saved the frame pointer of {caller_name} here when it started, and restores it when it returns.\n"
                f"The address of this slot, 0x{fo.addr:x}, is {fr.func}()'s own frame pointer: "
                "its local variables are found relative to it."
            )
            cell = Cell(
                fo.addr, 8, f"0x{v:x}", kind, True,
                _overlaps(self.changed, fo.addr, 8), "", target=target, target_status=status, target_size=8,
                tooltip=tip, raw=data, problem=_overlaps(self.problem_ranges, fo.addr, 8) or not intact, always_text=True,
            )
            out.append(FrameSlot("savedfp", cell, desc, None, intact))
        return out

    def frame_notes(self, step: int) -> list[str]:
        """Explanations of what happened to return addresses and frame pointers in this step."""
        tr = self.trace
        st = tr.steps[step]
        notes = []
        if step > 0:
            current = {fr.id for fr in tr.frame_chain(step)}
            for fr in reversed(tr.frame_chain(step - 1)):
                if fr.id in current:
                    continue
                if fr.parent is None:
                    notes.append(
                        f"main() returned: the CPU read the return address 0x{fr.ret_addr:x} from main()'s frame and "
                        "jumped back into the C runtime, which ends the program."
                    )
                else:
                    caller = tr.frames[fr.parent].func
                    notes.append(
                        f"{fr.func}() returned: the CPU read the return address 0x{fr.ret_addr:x} from its frame and jumped back to "
                        f"{tr.describe_code(fr.ret_addr)}. {caller}()'s frame pointer was restored from the saved copy (0x{fr.saved_fp:x})."
                    )
        if st.kind in ("call", "start") and st.frame is not None:
            fr = tr.frames[st.frame]
            fp_slot = tr.objects[fr.fp_obj].addr if fr.fp_obj is not None else 0
            if fr.parent is None:
                notes.append(
                    f"main() is called by the C runtime. The top of its frame holds the return address 0x{fr.ret_addr:x} "
                    "(back into the runtime) and a saved frame pointer of 0, because there is no frame above it."
                )
            else:
                caller = tr.frames[fr.parent].func
                notes.append(
                    f"The call pushed the return address 0x{fr.ret_addr:x} onto the stack: when {fr.func}() is done, the program "
                    f"continues at {tr.describe_code(fr.ret_addr)}. Then {fr.func}() saved {caller}()'s frame pointer "
                    f"(0x{fr.saved_fp:x}) right below it. The address of that slot, 0x{fp_slot:x}, is {fr.func}()'s own frame pointer."
                )
        return notes

    def build_statics(self, alive: list[MemObject]) -> list[VarView]:
        out = []
        for o in alive:
            if o.kind in ("global", "static") and not o.hidden:
                name = o.name if o.kind == "global" else f"{o.name} (static in {o.owner}())" if o.owner else o.name
                out.append(self.var_view(o, name=name))
        return out

    def build_blocks(self, step: int, live: list[MemObject], reachable: set[int], show_freed: bool) -> list[BlockView]:
        tr = self.trace
        views = []
        for o in live:
            status = "live" if o.id in reachable else "leaked"
            views.append(self.block_view(o, status))
        if show_freed:
            freed = [
                o for o in tr.objects
                if o.kind == "heap" and o.death is not None and o.death <= step and o.death_reason in ("freed", "realloc")
            ]
            freed.sort(key=lambda o: o.death, reverse=True)
            shown = 0
            for o in freed:
                if shown >= MAX_FREED_SHOWN:
                    break
                if any(v.addr < o.addr + max(o.size, 1) and o.addr < v.addr + max(v.size, 1) for v in views):
                    continue
                views.append(self.block_view(o, "freed"))
                shown += 1
        views.sort(key=lambda b: b.addr)
        return views

    def block_view(self, o: MemObject, status: str) -> BlockView:
        hint = o.hint if o.hint is not None and o.hint_step <= self.step else None
        if hint is not None and hint.size and o.size >= hint.size:
            n = o.size // hint.size
            ty = hint if n == 1 else T.ArrayType(hint, n)
            value = self.value(ty, o.addr, "")
            rest = o.size - n * hint.size
            if rest:
                extra = self.raw_bytes(o.addr + n * hint.size, rest, "rest")
                if isinstance(value, ArrayView):
                    value.elems.append(extra)
                else:
                    value = ArrayView(o.addr, o.size, "", [value, extra], T.type_str(hint), n)
            type_name = T.type_str(ty)
        else:
            value = self.raw_bytes(o.addr, o.size, "")
            type_name = f"{o.size} bytes"
        return BlockView(
            obj_id=o.id,
            label=o.label,
            addr=o.addr,
            size=o.size,
            chunk=o.chunk,
            status=status,
            alloc_line=o.line,
            free_line=o.free_line,
            type_name=type_name,
            value=value,
            changed=_overlaps(self.changed, o.addr, max(o.size, 1)),
            is_new=o.birth == self.step,
            problem=_overlaps(self.problem_ranges, o.addr, max(o.size, 1)),
        )

    def var_view(self, o: MemObject, name: str | None = None) -> VarView:
        return VarView(
            obj_id=o.id,
            name=name or o.name,
            type_name=T.type_str(o.ty) if o.ty is not None else f"{o.size} bytes",
            addr=o.addr,
            size=o.size,
            value=self.value(o.ty, o.addr, ""),
            changed=_overlaps(self.changed, o.addr, o.size),
            is_new=o.birth == self.step,
            problem=_overlaps(self.problem_ranges, o.addr, o.size),
        )

    # ------------------------------------------------------------- values

    def raw_bytes(self, addr: int, size: int, label: str) -> ArrayView:
        cells = []
        shown = min(size, MAX_ARRAY_CELLS)
        for i in range(shown):
            cells.append(self.scalar(T.UCHAR, addr + i, f"+{i}", raw_byte=True))
        return ArrayView(addr, size, label, cells, "byte", size, hidden=size - shown, changed=_overlaps(self.changed, addr, size))

    def value(self, ty: T.CType, addr: int, label: str):
        if isinstance(ty, T.ArrayType):
            n = ty.length or 0
            esz = ty.elem.size
            if n <= MAX_ARRAY_CELLS:
                idxs = list(range(n))
            else:
                idxs = list(range(MAX_ARRAY_CELLS - 4)) + list(range(n - 4, n))
            elems = [self.value(ty.elem, addr + i * esz, f"[{i}]") for i in idxs]
            text = None
            if T.is_char_array(ty):
                text = self.string_preview(addr, ty.size)
            return ArrayView(addr, ty.size, label, elems, T.type_str(ty.elem), n, hidden=n - len(idxs), text=text, changed=_overlaps(self.changed, addr, ty.size))
        if isinstance(ty, T.StructType):
            fields = [(f.name, self.value(f.type, addr + f.offset, f.name)) for f in ty.fields]
            return StructView(addr, ty.size, label, fields, T.type_str(ty), changed=_overlaps(self.changed, addr, ty.size))
        if ty is None:
            return self.raw_bytes(addr, 1, label)
        return self.scalar(ty, addr, label)

    def string_preview(self, addr: int, size: int) -> str | None:
        got = self.mem.peek(addr, size)
        if got is None:
            return None
        data, init = got
        k = data.find(0)
        if k < 0:
            return None
        if 0 in init[: k + 1]:
            return None
        return data[:k].decode("utf-8", "replace")

    def scalar(self, ty: T.CType, addr: int, label: str, raw_byte: bool = False) -> Cell:
        size = ty.size
        got = self.mem.peek(addr, size)
        changed = _overlaps(self.changed, addr, size)
        problem = _overlaps(self.problem_ranges, addr, size)
        if got is None:
            return Cell(addr, size, "??", "raw", False, changed, label, problem=problem)
        data, init_b = got
        init = 0 not in init_b
        tname = T.type_str(ty)
        if raw_byte:
            v = data[0]
            text = f"{v:02x}" if init else "??"
            tip = f"byte at 0x{addr:x}: 0x{v:02x} ({v})" + ("" if init else " - not initialized (garbage)")
            return Cell(addr, 1, text, "raw", init, changed, label, tooltip=tip, raw=data, problem=problem)
        if isinstance(ty, T.PointerType):
            a = int.from_bytes(data, "little")
            if not init:
                status = "uninit"
                text = "?"
            elif a == 0:
                status = "null"
                text = "NULL"
            elif isinstance(ty.target, T.FunctionType):
                fname = self.trace.code_names.get(a)
                status = "fn" if fname else "wild"
                text = f"{fname}()" if fname else f"0x{a:x}"
            else:
                status = self.pointer_status(a)
                text = f"0x{a:x}"
            tip = f"{tname} at 0x{addr:x}\nvalue: {('0x%x' % a) if init else 'garbage (never set)'}"
            origin = self.cursor.prov.get(addr) if init and a and not addr & 7 else None
            o = self.trace.objects[origin] if origin is not None else None
            if o is not None and not o.alive_at(self.step) and o.kind in ("heap", "local", "param") and status != "fn":
                status = "dangling"
                if o.kind == "heap":
                    how = "replaced by realloc" if o.death_reason == "realloc" else "freed"
                    tip += f"\nDANGLING: it points into {o.label}, which was {how} at line {o.free_line}"
                else:
                    tip += f"\nDANGLING: it points to '{o.name}', which no longer exists"
                now = self.index.find(a)
                if now is not None:
                    tip += f".\nThat memory now belongs to {now.describe()}!"
            elif status == "dangling":
                tip += "\nDANGLING: points to memory that was freed / no longer exists"
            elif status == "wild":
                tip += "\npoints to memory that does not belong to any variable"
            elif status == "uninit":
                tip += "\nThis pointer was never set; its value is garbage."
            kind = {"null": "null", "fn": "fn"}.get(status, "ptr")
            tsize = ty.target.size if ty.target.complete and not isinstance(ty.target, T.FunctionType) else 1
            return Cell(addr, size, text, kind, init, changed, label, target=a if init and a and status != "fn" else None, target_status=status, target_size=tsize, tooltip=tip, raw=data, problem=problem)
        v = decode(ty, data)
        if isinstance(ty, T.FloatType):
            text = fmt_float(v)
            kind = "float"
        elif isinstance(ty, T.IntType) and ty.rank == 0:
            text = "true" if v else "false"
            kind = "bool"
        elif isinstance(ty, T.IntType) and ty.size == 1 and ty.rank == 1:
            text = char_repr(v)
            kind = "char"
        else:
            text = str(v)
            kind = "int"
        tip = f"{tname} at 0x{addr:x} ({size} byte{'s' if size != 1 else ''})\nvalue: {text}"
        if kind == "char":
            tip += f" = {v}"
        if not init:
            tip += "\nNOT INITIALIZED - this is leftover garbage"
        return Cell(addr, size, text, kind, init, changed, label, tooltip=tip, raw=data, problem=problem)

    def pointer_status(self, a: int) -> str:
        o = self.index.find(a)
        if o is not None:
            return "ok"
        if self.index.find_end(a) is not None:
            return "end"
        # dead object?
        for o in reversed(self.trace.objects):
            if o.birth <= self.step and o.death is not None and o.death <= self.step and o.addr <= a < o.addr + max(o.size, 1) and o.kind in ("heap", "local", "param"):
                return "dangling"
        return "wild"

    def _collect_targets(self, v, out: set) -> None:
        if isinstance(v, Cell):
            if v.target is not None:
                out.add(v.target)
        elif isinstance(v, ArrayView):
            for e in v.elems:
                self._collect_targets(e, out)
        elif isinstance(v, StructView):
            for _, f in v.fields:
                self._collect_targets(f, out)

    # ----------------------------------------------------------- leaks

    def reachable(self, alive: list[MemObject], heap_live: list[MemObject]) -> set[int]:
        if not heap_live:
            return set()
        blocks = sorted(heap_live, key=lambda o: o.addr)
        starts = [o.addr for o in blocks]

        def hit(a: int) -> MemObject | None:
            i = bisect.bisect_right(starts, a) - 1
            if i >= 0:
                o = blocks[i]
                if o.addr <= a <= o.addr + o.size:
                    return o
            return None

        seen: set[int] = set()
        work: list[MemObject] = []

        def scan(addr: int, size: int) -> None:
            got = self.mem.peek(addr, min(size, MAX_SCAN_BYTES))
            if got is None:
                return
            data, init = got
            start = (-addr) % 8
            for off in range(start, len(data) - 7, 8):
                if init[off] and init[off + 7]:
                    a = int.from_bytes(data[off : off + 8], "little")
                    if a:
                        o = hit(a)
                        if o is not None and o.id not in seen:
                            seen.add(o.id)
                            work.append(o)

        for o in alive:
            if o.kind in ("local", "param", "global", "static", "builtin"):
                scan(o.addr, o.size)
        # A function that is returning still "holds" its return value.
        st = self.trace.steps[self.step]
        if st.kind == "return" and st.frame is not None:
            fr = self.trace.frames[st.frame]
            if fr.ret_text and fr.ret_text.startswith("0x"):
                try:
                    o = hit(int(fr.ret_text, 16))
                except ValueError:
                    o = None
                if o is not None and o.id not in seen:
                    seen.add(o.id)
                    work.append(o)
        while work:
            o = work.pop()
            scan(o.addr, o.size)
        return seen

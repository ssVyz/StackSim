"""Executes a checked C program on the virtual memory and records a Trace.

A *step* is recorded before every statement (and at function entry, return
and loop iterations). Between two steps the VM collects memory writes,
explanations ("events") and problems ("diagnostics") so the GUI can show,
for every step, what the memory looked like and what just happened.
"""

from __future__ import annotations

import math
import sys
import threading

from ..c import ast as A
from ..c import ctype as T
from ..c.ast import expr_str
from ..c.errors import Diagnostic, Loc
from ..c.sema import Program, Symbol
from .heap import Heap
from .memory import (
    CODE_BASE,
    DATA_BASE,
    HEAP_BASE_MIN,
    MASK64,
    NULL_PAGE,
    RODATA_BASE,
    STACK_SIZE,
    STACK_TOP,
    Memory,
    Region,
    zeros,
)
from .trace import FrameRec, MemObject, Step, Trace
from ..c.ctype import float32
from .values import Blob, Ptr, decode, encode, fmt_value, truthy

NORMAL, BREAK, CONTINUE, RETURN = 0, 1, 2, 3
DEFAULT_MAX_STEPS = 50000
DEFAULT_MAX_FRAMES = 15  # nested function calls (main included); a teaching limit, see push_frame
WARNINGS_PER_SITE = 3


class FatalError(Exception):
    def __init__(self, diag: Diagnostic):
        super().__init__(diag.message)
        self.diag = diag


class ProgramExit(Exception):
    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


class StepLimit(Exception):
    pass


class Cancelled(Exception):
    pass


class _At:
    """A stand-in for an AST node when a problem belongs to a plain source location."""

    __slots__ = ("loc",)

    def __init__(self, loc):
        self.loc = loc


RUNTIME_SIZE = 0x40  # the simulated C runtime / library code at the start of the code region


def _align_up(v: int, a: int) -> int:
    return (v + a - 1) // a * a


def _path_in(ty: T.CType | None, off: int) -> str:
    """Describe the element/member at byte offset ``off`` inside a value of type ``ty``."""
    if ty is None:
        return ""
    if isinstance(ty, T.ArrayType) and ty.elem.size:
        i = off // ty.elem.size
        return f"[{i}]" + _path_in(ty.elem, off - i * ty.elem.size)
    if isinstance(ty, T.StructType):
        for f in ty.fields:
            if f.offset <= off < f.offset + f.type.size:
                return f".{f.name}" + _path_in(f.type, off - f.offset)
    return ""


class VM:
    def __init__(
        self,
        program: Program,
        stdin: bytes = b"",
        max_steps: int = DEFAULT_MAX_STEPS,
        cancel: threading.Event | None = None,
        source: str = "",
        max_frames: int = DEFAULT_MAX_FRAMES,
        input_provider=None,
        stdin_eof: bool = False,
        args: list[str] | None = None,
        prog_name: str = "./prog",
    ):
        self.prog = program
        self.source = source
        self.stdin = stdin
        self.stdin_pos = 0
        self.max_steps = max_steps
        self.max_frames = max_frames
        # Interactive input: called (in the VM's thread) when a program wants to
        # read more than the prepared input. It returns the bytes the user typed,
        # or None for "end of input"; it may raise Cancelled to stop the run.
        self.input_provider = input_provider
        self.stdin_eof = stdin_eof
        # Command-line arguments (argv[1], argv[2], ...); argv[0] is prog_name.
        self.args = list(args or [])
        self.prog_name = prog_name
        self.arg_objs: list[int] = []
        self.cancel = cancel

        self.mem = Memory()
        self.objects: list[MemObject] = []
        self.frames: list[FrameRec] = []
        self.steps: list[Step] = []
        self.diags: list[Diagnostic] = []
        self.pending: list[int] = []
        self.reads: list[tuple[int, int]] = []  # memory read since the last step (shown in the GUI)
        self.events: list[str] = []
        self.output: list[tuple[str, bytes]] = []
        self.frame: FrameRec | None = None
        self.sp = STACK_TOP - 0x100
        self.shadow: dict[int, int] = {}
        self.warn_counts: dict = {}
        self.code_map: dict[int, A.FuncDef] = {}
        self.func_addr: dict[str, int] = {}
        self.func_first_line: dict[str, int] = {}
        self.code_ranges: list[tuple[int, int, str]] = []
        self.return_sites: dict[int, tuple[str, int | None]] = {}
        self.runtime_return = CODE_BASE + 0x1D
        self.literal_ref: list[tuple[int, int]] = []
        self.file_streams: dict[int, str] = {}
        self.static_objs: list[int] = []
        self.heap: Heap | None = None
        self.heap_counter = 0
        self.rand_state = None
        self.stack: Region = self.mem.add_region("stack", "stack", STACK_TOP - STACK_SIZE, STACK_SIZE)

        from . import libc

        self.builtins = libc.BUILTINS

        self._rv = {
            A.IntLit: self.rv_IntLit, A.FloatLit: self.rv_FloatLit, A.Ident: self.rv_Ident,
            A.Decay: self.rv_Decay, A.Cast: self.rv_Cast, A.Unary: self.rv_Unary, A.Postfix: self.rv_Postfix,
            A.Binary: self.rv_Binary, A.Assign: self.rv_Assign, A.Cond: self.rv_Cond, A.Comma: self.rv_Comma,
            A.Call: self.rv_Call, A.Index: self.rv_Index, A.Member: self.rv_Member,
        }
        self._lv = {
            A.Ident: self.lv_Ident, A.Unary: self.lv_Unary, A.Index: self.lv_Index,
            A.Member: self.lv_Member, A.StrLit: self.lv_StrLit, A.Call: self.lv_Call,
            A.Assign: self.lv_temp, A.Cond: self.lv_temp, A.Comma: self.lv_temp,
        }
        self._ex = {
            A.ExprStmt: self.ex_ExprStmt, A.DeclStmt: self.ex_DeclStmt, A.Compound: self.ex_Compound,
            A.If: self.ex_If, A.While: self.ex_While, A.DoWhile: self.ex_DoWhile, A.For: self.ex_For,
            A.Switch: self.ex_Switch, A.Case: self.ex_Case, A.Break: self.ex_Break,
            A.Continue: self.ex_Continue, A.Return: self.ex_Return, A.Empty: self.ex_Empty,
        }

    # ================================================================ trace

    def snap(self, line: int | None, kind: str, force: bool = False) -> None:
        steps = self.steps
        if not force:
            if len(steps) >= self.max_steps:
                raise StepLimit()
            if self.cancel is not None and self.cancel.is_set():
                raise Cancelled()
        log = self.mem.log
        self.mem.log = []
        fr = self.frame
        reads = tuple(dict.fromkeys(self.reads)) if self.reads else ()
        steps.append(Step(line, kind, fr.id if fr is not None else None, log, self.events, len(self.output), self.stdin_pos, self.pending, self.sp, reads))
        self.events = []
        self.pending = []
        self.reads = []

    def event(self, text: str) -> None:
        self.events.append(text)

    def warn(self, kind: str, msg: str, node, hint: str | None = None, addrs=(), key=None) -> None:
        loc = node.loc if node is not None else None
        line = loc.line if loc is not None else None
        k = key if key is not None else (kind, line)
        c = self.warn_counts.get(k, 0) + 1
        self.warn_counts[k] = c
        if c > WARNINGS_PER_SITE:
            if c == WARNINGS_PER_SITE + 1:
                d = Diagnostic("note", f"further '{kind}' problems on this line are not reported again", loc, step=len(self.steps), kind=kind)
                self.diags.append(d)
                self.pending.append(len(self.diags) - 1)
            return
        d = Diagnostic("warning", msg, loc, hint, step=len(self.steps), kind=kind, addrs=list(addrs))
        self.diags.append(d)
        self.pending.append(len(self.diags) - 1)

    def fatal(self, kind: str, msg: str, node, hint: str | None = None, addrs=()):
        loc = node.loc if node is not None else None
        raise FatalError(Diagnostic("error", msg, loc, hint, step=None, kind=kind, addrs=list(addrs)))

    def need_input(self, node, func: str) -> bool:
        """Ask for more input when the program has read everything. False means end of input."""
        if self.stdin_eof or self.input_provider is None:
            return False
        tail = b"".join(d for _, d in self.output[-20:]).decode("utf-8", "replace")
        request = {
            "line": node.loc.line if node is not None and getattr(node, "loc", None) else None,
            "func": func,
            "call": expr_str(node) if isinstance(node, A.Call) else func + "()",
            "output": tail.rstrip("\n").rsplit("\n", 1)[-1],
            "format": None,
        }
        if func == "scanf" and isinstance(node, A.Call) and node.args and isinstance(node.args[0], A.Decay) and isinstance(node.args[0].expr, A.StrLit):
            request["format"] = node.args[0].expr.data.decode("utf-8", "replace")
        data = self.input_provider(request)
        if data is None:
            self.stdin_eof = True
            self.event(f"The input was ended (EOF), so {func}() gets nothing more to read.")
            return False
        self.stdin = self.stdin + data
        shown = data.decode("utf-8", "replace").strip("\n").replace("\n", "\\n")
        self.event(f'You typed "{shown}" as input for {func}().')
        return True

    def write_out(self, stream: str, data: bytes) -> None:
        if data:
            self.output.append((stream, bytes(data)))

    # ============================================================== objects

    def new_object(self, kind: str, name: str, ty, addr: int, size: int, line: int | None, region: Region) -> int:
        oid = len(self.objects)
        o = MemObject(oid, kind, name, ty, addr, size, len(self.steps), line)
        o.region = region
        self.objects.append(o)
        return oid

    def kill(self, oid: int, reason: str) -> None:
        o = self.objects[oid]
        if o.death is None:
            o.death = len(self.steps)
            o.death_reason = reason

    def find_object(self, addr: int, size: int = 1, exclude: int | None = None) -> MemObject | None:
        """The live object overlapping [addr, addr+size), if any."""
        end = addr + size
        r = self.mem.region_at(addr)
        if r is None:
            return None
        if r.kind == "stack":
            fr = self.frame
            while fr is not None:
                for oid in fr.vars:
                    o = self.objects[oid]
                    if o.death is None and oid != exclude and o.addr < end and addr < o.addr + o.size:
                        return o
                for oid in (fr.ret_obj, fr.fp_obj):
                    if oid is not None:
                        o = self.objects[oid]
                        if o.death is None and o.addr < end and addr < o.addr + o.size:
                            return o
                fr = self.frames[fr.parent] if fr.parent is not None else None
            for oid in self.arg_objs:
                o = self.objects[oid]
                if oid != exclude and o.addr < end and addr < o.addr + o.size:
                    return o
            return None
        if r.kind == "heap":
            for oid in self.heap.live.values():
                o = self.objects[oid]
                if oid != exclude and o.addr < end and addr < o.addr + max(o.size, 1):
                    return o
            return None
        for oid in self.static_objs:
            o = self.objects[oid]
            if oid != exclude and o.addr < end and addr < o.addr + o.size:
                return o
        return None

    def where(self, o: MemObject, addr: int) -> str:
        """A name for the location ``addr`` inside object ``o``, e.g. "arr[3]" or "p->next"."""
        off = addr - o.addr
        if o.kind == "heap":
            hint = o.hint
            if hint is not None and hint.size:
                ty = T.ArrayType(hint, max(1, o.size // hint.size)) if not isinstance(hint, T.StructType) or o.size >= 2 * hint.size else hint
                path = _path_in(ty, off)
                return f"{o.label}{path}" if path else o.label
            return f"byte {off} of {o.label}"
        path = _path_in(o.ty, off)
        return f"{o.name}{path}"

    # ======================================================= memory access

    def access(self, addr: int, size: int, prov, write: bool, node) -> Region:
        if prov is not None:
            o = self.objects[prov]
            if o.death is None and o.addr <= addr and addr + size <= o.addr + o.size:
                r = o.region
                if write and r.readonly:
                    self.readonly_write(node, addr, size)
                return r
        return self.access_slow(addr, size, prov, write, node)

    def access_slow(self, addr: int, size: int, prov, write: bool, node, quiet: bool = False) -> Region:
        verb = "writing to" if write else "reading"
        if addr < NULL_PAGE or addr > MASK64 - 0xFFFF:
            if addr == 0:
                msg = f"NULL pointer dereference: {verb} memory through a pointer that is NULL"
            else:
                msg = f"NULL pointer dereference: {verb} address 0x{addr:x}, which is next to NULL (a pointer that is NULL plus an offset)"
            self.fatal(
                "null-pointer",
                msg,
                node,
                hint="A NULL pointer points to nothing. Check pointers before using them, e.g. 'if (p != NULL)'. "
                "malloc() returns NULL when it cannot allocate memory.",
            )
        r = self.mem.region_at(addr, size)
        if r is None or r.kind == "code":
            if r is not None:
                msg = f"segmentation fault: {verb} address 0x{addr:x}, which holds a function's machine code, not data"
                hint = None
            elif self.mem.region_at(addr) is not None:
                msg = f"segmentation fault: {verb} {size} bytes at 0x{addr:x} runs past the end of the program's memory"
                hint = "An index or pointer has gone far beyond the end of the memory that belongs to the program."
            else:
                msg = f"segmentation fault: {verb} address 0x{addr:x}, which is not part of the program's memory"
                hint = "The pointer holds an address that was never valid - typically an uninitialized pointer or a garbage value. Always set pointers before using them (to NULL, to &variable, or to the result of malloc)."
            self.fatal("segfault", msg, node, hint=hint, addrs=[(addr, size)])
        if not quiet:
            o = self.objects[prov] if prov is not None else None
            if o is not None:
                if o.death is not None:
                    self.dead_access(o, addr, size, write, node)
                else:
                    self.oob_access(o, addr, size, write, node)
            else:
                hit = self.find_object(addr, size)
                if hit is None:
                    if r.kind == "stack" and addr < self.sp:
                        where = "stack memory below the current stack frames: it belongs to no live variable (most likely to a function that has already returned)"
                    elif r.kind == "heap":
                        where = "heap memory that is not inside any allocated block (it was freed, or it is malloc's own bookkeeping)"
                    else:
                        where = "memory that does not belong to any variable"
                    self.warn("wild-pointer", f"{verb} 0x{addr:x}: this is {where}", node, addrs=[(addr, size)])
        if write and r.readonly:
            self.readonly_write(node, addr, size)
        return r

    def readonly_write(self, node, addr: int, size: int) -> None:
        self.warn(
            "read-only",
            "writing into a string literal",
            node,
            hint="String literals like \"hello\" are stored in read-only memory; on a real system this crashes. "
            "To get a modifiable string, copy it into an array: char s[] = \"hello\";",
            addrs=[(addr, size)],
        )

    def dead_access(self, o: MemObject, addr: int, size: int, write: bool, node) -> None:
        verb = "writing to" if write else "reading"
        now = self.find_object(addr, size)
        if o.kind == "heap":
            if o.death_reason == "realloc":
                msg = f"use after realloc: {verb} {o.label}, which realloc() replaced at line {o.free_line}"
                hint = "realloc() may move the block. After calling it, only use the pointer it returned."
            else:
                msg = f"use after free: {verb} {o.label}, which was freed at line {o.free_line}"
                hint = "After free(p), p still holds the old address (a 'dangling pointer'), but the memory no longer belongs to the program. Set p = NULL right after free(p)."
            if now is not None:
                msg += f" - that memory now belongs to {now.describe()}!"
            self.warn("use-after-free", msg, node, hint=hint, addrs=[(addr, size)])
            return
        if o.kind in ("local", "param"):
            fr = self.frames[o.frame] if o.frame is not None else None
            fname = f"{fr.func}()" if fr is not None else "a function"
            if o.death_reason == "scope":
                msg = f"dangling pointer: {verb} '{o.name}', whose block {'in ' + fname if fr else ''} has already ended"
            else:
                msg = f"dangling pointer: {verb} '{o.name}', a local variable of {fname}, which has already returned"
            if now is not None:
                msg += f" - that stack memory is now used by {now.describe()}!"
            hint = "Local variables only exist while their function (or block) runs. Return the value itself, or use malloc() if the data must outlive the function."
            self.warn("dangling-pointer", msg, node, hint=hint, addrs=[(addr, size)])
            return
        self.warn("dangling-pointer", f"{verb} {o.describe()}, which no longer exists", node, addrs=[(addr, size)])

    def oob_access(self, o: MemObject, addr: int, size: int, write: bool, node) -> None:
        verb = "writing" if write else "reading"
        what = o.describe()
        idx_text = ""
        ty = o.ty if o.kind != "heap" else o.hint
        if isinstance(ty, T.ArrayType) and ty.elem.size:
            esz = ty.elem.size
            i = (addr - o.addr) // esz
            idx_text = f" (index {i}; valid indexes are 0 to {ty.length - 1})"
        elif o.kind == "heap" and ty is not None and ty.size and not isinstance(ty, T.StructType):
            n = o.size // ty.size
            i = (addr - o.addr) // ty.size
            idx_text = f" (element {i}; the block has room for {n} element{'s' if n != 1 else ''}, 0 to {n - 1})" if n else ""
        side = "before the start" if addr < o.addr else "past the end"
        msg = f"out of bounds: {verb} {size} byte{'s' if size != 1 else ''} {side} of {what}{idx_text}"
        hit = self.find_object(addr, size, exclude=o.id)
        if hit is not None:
            msg += f" - this memory belongs to {self.where(hit, max(addr, hit.addr)) if hit.kind not in ('retaddr', 'savedfp') else hit.describe()}!"
        elif o.kind == "heap":
            msg += " - this memory is outside the block (it may hold malloc's bookkeeping data)"
        hint = (
            "C does not check array bounds. Writing past the end silently overwrites whatever lies next to it in memory."
            if write
            else "C does not check array bounds. Reading past the end returns whatever happens to be stored there."
        )
        self.warn("out-of-bounds", msg, node, hint=hint, addrs=[(addr, size)])

    def uninit_read(self, addr: int, size: int, prov, node) -> None:
        o = self.objects[prov] if prov is not None else self.find_object(addr, size)
        if o is None:
            what = f"memory at 0x{addr:x}"
            hint = None
        elif o.kind == "heap":
            what = self.where(o, addr)
            hint = "malloc() does not clear the memory it returns. Assign values first, or use calloc(), which sets everything to 0."
        else:
            what = f"'{self.where(o, addr)}'"
            hint = "Local variables are not set to 0 automatically; they contain whatever was in memory before. Give them a value first, e.g. 'int x = 0;'."
        self.warn("uninitialized", f"using {what}, which was never given a value - it contains leftover garbage", node, hint=hint, addrs=[(addr, size)])

    def note_read(self, addr: int, size: int) -> None:
        if size > 0:
            self.reads.append((addr, size))

    def load(self, addr: int, ty: T.CType, prov, node):
        size = ty.size
        r = self.access(addr, size, prov, False, node)
        self.reads.append((addr, size))
        off = addr - r.base
        end = off + size
        cls = ty.__class__
        if cls is T.StructType:
            sh = self.shadow
            ptrs = {}
            if sh:
                for k in range(_align_up(addr, 8), addr + size, 8):
                    p = sh.get(k)
                    if p is not None:
                        ptrs[k - addr] = p
            return Blob(bytes(r.data[off:end]), bytes(r.init[off:end]), ptrs)
        if 0 in r.init[off:end]:
            self.uninit_read(addr, size, prov, node)
        data = r.data[off:end]
        if cls is T.PointerType:
            return Ptr(int.from_bytes(data, "little"), None if addr & 7 else self.shadow.get(addr))
        if cls is T.FloatType:
            return decode(ty, bytes(data))
        if cls is T.IntType or cls is T.EnumType:
            v = int.from_bytes(data, "little", signed=ty.signed)
            if ty.rank == 0:
                return 1 if v else 0
            return v
        self.fatal("internal", f"cannot load a value of type {T.type_str(ty)}", node)

    def store(self, addr: int, ty: T.CType, v, prov, node) -> None:
        size = ty.size
        r = self.access(addr, size, prov, True, node)
        cls = ty.__class__
        if cls is T.StructType:
            self.mem.write(r, addr, v.data, v.init)
        else:
            self.mem.write(r, addr, encode(ty, v))
        sh = self.shadow
        is_ptr = cls is T.PointerType
        if sh:
            for k in range(addr & ~7, addr + size, 8):
                if k in sh and not (is_ptr and k == addr):
                    self.sh_set(k, None)
        if is_ptr:
            if not addr & 7:
                self.sh_set(addr, v.prov)
        elif cls is T.StructType and v.ptrs:
            for off, p in v.ptrs.items():
                if not (addr + off) & 7:
                    self.sh_set(addr + off, p)

    # Pointer provenance ("shadow") is kept per 8-byte-aligned address. Changes
    # are logged next to the memory writes so the GUI can replay them too.
    def sh_set(self, k: int, v: int | None) -> None:
        sh = self.shadow
        old = sh.get(k)
        if old == v:
            return
        if v is None:
            del sh[k]
        else:
            sh[k] = v
        self.mem.log.append((k, old, v))

    def clear_shadow(self, addr: int, size: int) -> None:
        sh = self.shadow
        if not sh:
            return
        if size > 512:
            keys = [k for k in sh if addr - 8 < k < addr + size]
        else:
            keys = [k for k in range(addr & ~7, addr + size, 8) if k in sh]
        for k in keys:
            self.sh_set(k, None)

    def copy_shadow(self, dst: int, src: int, size: int) -> None:
        sh = self.shadow
        if not sh:
            return
        moved = {k - src + dst: v for k, v in sh.items() if src <= k and k + 8 <= src + size}
        self.clear_shadow(dst, size)
        for k, v in moved.items():
            if not k & 7:
                self.sh_set(k, v)

    # ============================================================ program

    def load_program(self) -> None:
        prog = self.prog
        mem = self.mem
        # Code: a small C runtime/library stub, then the functions. Every source
        # line of a function gets 16 bytes of (imaginary) machine code, so that a
        # return address can be traced back to the line of the call.
        pos = CODE_BASE + RUNTIME_SIZE
        for sym in prog.func_symbols:
            fd = prog.functions[sym.name]
            first, last = fd.loc.line, fd.body.end_loc.line
            size = _align_up(0x10 * (last - first + 2), 0x40)
            self.code_map[pos] = fd
            self.func_addr[fd.name] = pos
            self.func_first_line[fd.name] = first
            self.code_ranges.append((pos, pos + size, fd.name))
            pos += size
        code = mem.add_region("code", "code", CODE_BASE, pos - CODE_BASE, readonly=True, fill="zero")
        code.init[:] = b"\x01" * len(code.init)
        self.return_sites[self.runtime_return] = ("the C runtime (which then ends the program)", None)

        # Read-only data: string literals.
        lit_size = sum(len(b) + 1 for b in prog.literals)
        rodata = mem.add_region("rodata", "rodata", RODATA_BASE, max(16, _align_up(lit_size, 16)), readonly=True, fill="zero")
        pos = RODATA_BASE
        for data in prog.literals:
            n = len(data) + 1
            oid = self.new_object("literal", '"' + data.decode("utf-8", "replace") + '"', T.ArrayType(T.CHAR, n), pos, n, None, rodata)
            self.static_objs.append(oid)
            mem.write(rodata, pos, data + b"\0")
            self.literal_ref.append((pos, oid))
            pos += n

        # Data: FILE objects, standard streams, globals and static locals.
        data_base = max(DATA_BASE, _align_up(rodata.end, 0x1000))
        layout: list = []
        pos = data_base
        file_objs = {}
        for name in ("stdin", "stdout", "stderr"):
            layout.append(("file", name, pos, 16))
            file_objs[name] = pos
            pos += 16
        for sym in prog.builtin_globals:
            pos = _align_up(pos, sym.ty.align)
            layout.append(("builtin", sym, pos, sym.ty.size))
            pos += sym.ty.size
        for d in prog.globals:
            pos = _align_up(pos, d.ty.align)
            layout.append(("var", d, pos, d.ty.size))
            pos += d.ty.size
        data = mem.add_region("data", "data", data_base, max(16, _align_up(pos - data_base, 16)), fill="zero")
        for kind, what, addr, size in layout:
            if kind == "file":
                oid = self.new_object("builtin", f"<{what} FILE>", None, addr, size, None, data)
                self.objects[oid].hidden = True
                self.file_streams[addr] = what
                mem.write(data, addr, zeros(size))
            elif kind == "builtin":
                sym = what
                oid = self.new_object("builtin", sym.name, sym.ty, addr, size, None, data)
                self.objects[oid].hidden = True
                sym.addr, sym.obj = addr, oid
                target = file_objs.get(sym.name, 0)
                mem.write(data, addr, target.to_bytes(8, "little"))
            else:
                d = what
                sym = d.sym
                kind2 = "static" if sym.storage == "static" else "global"
                oid = self.new_object(kind2, d.name, d.ty, addr, size, d.name_loc.line, data)
                self.objects[oid].owner = sym.owner
                sym.addr, sym.obj = addr, oid
            self.static_objs.append(oid)
        for d in prog.globals:
            sym = d.sym
            mem.write(data, sym.addr, zeros(d.ty.size))
            for off, ty, expr in d.init_plan:
                self.init_write(sym.addr + off, ty, expr, sym.obj)

        heap_base = max(HEAP_BASE_MIN, _align_up(data.end, 0x1000))
        self.heap = Heap(mem, heap_base)
        self.heap_base = heap_base

    def run(self) -> Trace:
        end = "exit"
        fatal = None
        code = None
        try:
            self.load_program()
            code = self.run_main()
        except ProgramExit as e:
            code = e.code
        except FatalError as e:
            fatal = e.diag
            end = "error"
        except StepLimit:
            end = "limit"
        except Cancelled:
            end = "cancelled"
        except RecursionError:
            fatal = Diagnostic("error", "the program nested function calls too deeply for the simulator", None, kind="stack-overflow")
            end = "error"
        self.finish(code, end, fatal)
        return Trace(
            source=self.source,
            stdin=self.stdin,
            steps=self.steps,
            objects=self.objects,
            frames=self.frames,
            output=self.output,
            diagnostics=self.diags,
            memory=self.mem,
            exit_code=code,
            end_reason=end,
            fatal=fatal,
            compile_diagnostics=list(self.prog.diagnostics),
            heap_base=self.heap.base if self.heap else 0,
            code_names={addr: fd.name for addr, fd in self.code_map.items()},
            code_ranges=list(self.code_ranges),
            return_sites=dict(self.return_sites),
            runtime_return=self.runtime_return,
            final_shadow=dict(self.shadow),
            args=[self.prog_name] + self.args,
        )

    def run_main(self) -> int:
        fd = self.prog.main
        args = []
        if len(fd.params) == 2:
            args = self.setup_args()
        self.push_frame(fd, None)
        self.bind_params(fd, args, [p.ty for p in fd.params])
        if args:
            n = args[0] - 1
            self.event(
                f"Before main() starts, the C runtime puts the {n} command-line argument{'s' if n != 1 else ''} "
                f"and the program name at the top of the stack: argc = {args[0]}, argv points to an array of strings."
            )
        self.event("The program starts: main() gets the first stack frame.")
        if self.prog.globals:
            self.event("Global variables were set up in the static data area before main() started.")
        self.snap(fd.loc.line, "start")
        returned = self.run_body(fd)
        if not returned:
            self.frame.ret_value = 0
            self.frame.ret_text = "0"
            self.event("main() reached its closing '}', which returns 0.")
            self.snap(fd.body.end_loc.line, "end")
        ret = self.frame.ret_value
        self.pop_frame()
        return ret if isinstance(ret, int) else 0

    def setup_args(self) -> list:
        """Put the program name, the arguments and the argv array at the top of the stack, like Linux does.

        Returns the values for main's parameters: [argc, argv].
        """
        words = [self.prog_name] + self.args
        encoded = [w.encode("utf-8") + b"\0" for w in words]
        st = self.stack
        top = STACK_TOP - 0x10
        # The strings, argv[0]'s text at the highest address.
        pos = top
        texts = []
        for i, data in enumerate(encoded):
            pos -= len(data)
            texts.append((pos, data))
        arr = (pos - 8 * (len(words) + 1)) & ~15
        if arr - 64 < st.base:
            self.fatal("stack-overflow", "the command-line arguments do not fit on the stack", None)
        for i, (addr, data) in enumerate(texts):
            oid = self.new_object("arg", f"argv[{i}] text", T.ArrayType(T.CHAR, len(data)), addr, len(data), None, st)
            self.arg_objs.append(oid)
            self.mem.write(st, addr, data)
        size = 8 * (len(words) + 1)
        aid = self.new_object("arg", "argv array", T.ArrayType(T.PointerType(T.CHAR), len(words) + 1), arr, size, None, st)
        self.arg_objs.append(aid)
        self.mem.write(st, arr, b"".join(a.to_bytes(8, "little") for a, _ in texts) + bytes(8))
        for i in range(len(texts)):
            self.sh_set(arr + 8 * i, self.arg_objs[i])
        self.sp = arr & ~15
        return [len(words), Ptr(arr, aid)]

    def finish(self, code, end: str, fatal: Diagnostic | None) -> None:
        line = None
        kind = "exit"
        if fatal is not None:
            fatal.step = len(self.steps)
            self.diags.append(fatal)
            self.pending.append(len(self.diags) - 1)
            line = fatal.line
            kind = "error"
            self.event("The program crashed: " + fatal.message)
        elif end == "limit":
            d = Diagnostic(
                "error",
                f"the program was stopped after {self.max_steps} steps",
                self.steps[-1].line and Loc(self.steps[-1].line, 1) if self.steps else None,
                hint="It may be stuck in an endless loop or endless recursion. If it is just long, raise the step limit in the settings.",
                step=len(self.steps),
                kind="step-limit",
            )
            self.diags.append(d)
            self.pending.append(len(self.diags) - 1)
            line = self.steps[-1].line if self.steps else None
            kind = "limit"
            self.event("Stopped: step limit reached.")
        elif end == "cancelled":
            kind = "limit"
            line = self.steps[-1].line if self.steps else None
            self.event("Stopped by the user.")
        else:
            # Normal end (return from main or exit()).
            while self.frame is not None:
                self.pop_frame(quiet=True)
            if self.heap is not None:
                leaked = [self.objects[o] for o in self.heap.live.values()]
                for o in leaked:
                    self.warn(
                        "memory-leak",
                        f"memory leak: {o.label} ({o.size} bytes, allocated at line {o.line}) was never freed",
                        None,
                        hint="Every block from malloc/calloc/realloc should be released with free() when it is no longer needed.",
                        addrs=[(o.addr, o.size)],
                        key=("leak", o.id),
                    )
                    self.diags[-1].loc = Loc(o.line, 1) if o.line else None
            self.event(f"The program finished with exit code {code if code is not None else 0}.")
            if self.heap is not None and self.heap.live:
                total = sum(self.objects[o].size for o in self.heap.live.values())
                self.event(f"{len(self.heap.live)} heap block(s) with {total} bytes were never freed (memory leak).")
        self.snap(line, kind, force=True)

    # ============================================================= frames

    def return_address(self, caller: FrameRec | None, node) -> int:
        """The address the CPU continues at after the call: right after the call in the caller's code."""
        if caller is None:
            return self.runtime_return
        has_loc = node is not None and getattr(node, "loc", None) is not None
        line = node.loc.line if has_loc else self.func_first_line[caller.func]
        col = node.loc.col if has_loc else 0
        if isinstance(node, A.Call) and node.fsym is not None and node.fsym.builtin:
            # A callback from a library function (qsort): it returns into the library.
            addr = CODE_BASE + 0x30 + col % 11
            self.return_sites[addr] = (f"{node.fsym.name}() in the C library (called from {caller.func}(), line {line})", line)
            return addr
        offset = 0x10 * (line - self.func_first_line[caller.func] + 1)
        addr = self.func_addr[caller.func] + offset + 5 + col % 11
        self.return_sites[addr] = (f"{caller.func}(), line {line}", line)
        return addr

    def push_frame(self, fd: A.FuncDef, call_line: int | None, node=None) -> FrameRec:
        parent = self.frame
        depth = (parent.depth + 1) if parent else 0
        # Like on x86-64, the stack is 16-byte aligned at every call (this may
        # leave a few unused bytes below the caller's variables).
        top = self.sp & ~15
        if depth + 1 > self.max_frames:
            self.frame_limit(fd, node)
        if top - 16 < self.stack.base:
            self.stack_overflow(fd, node)
        rec = FrameRec(len(self.frames), fd.name, parent.id if parent is not None else None, depth, call_line, len(self.steps), self.sp)
        rec.fd = fd
        self.frames.append(rec)
        # The call instruction pushes the return address; the function's prologue
        # then pushes the caller's frame pointer. The address of that saved frame
        # pointer becomes this frame's own frame pointer.
        ret_slot, fp_slot = top - 8, top - 16
        self.sp = fp_slot
        rec.ret_addr = self.return_address(parent, node)
        rec.saved_fp = self.objects[parent.fp_obj].addr if parent is not None else 0
        for kind, slot in (("retaddr", ret_slot), ("savedfp", fp_slot)):
            oid = self.new_object(kind, fd.name, None, slot, 8, None, self.stack)
            o = self.objects[oid]
            o.frame = rec.id
            o.hidden = True
            if kind == "retaddr":
                rec.ret_obj = oid
            else:
                rec.fp_obj = oid
        self.mem.write(self.stack, ret_slot, rec.ret_addr.to_bytes(8, "little"))
        self.mem.write(self.stack, fp_slot, rec.saved_fp.to_bytes(8, "little"))
        self.clear_shadow(fp_slot, 16)
        rec.blocks = [[self.sp, []]]
        self.frame = rec
        return rec

    def code_description(self, addr: int) -> str | None:
        site = self.return_sites.get(addr)
        if site is not None:
            return site[0]
        for start, end, name in self.code_ranges:
            if start <= addr < end:
                return f"somewhere inside {name}()"
        return None

    def check_frame_slots(self, rec: FrameRec) -> None:
        """Warn if the return address or the saved frame pointer was overwritten."""
        at = _At(rec.fd.body.end_loc) if rec.fd is not None else None
        ro, fo = self.objects[rec.ret_obj], self.objects[rec.fp_obj]
        base = self.stack.base
        cur_ret = int.from_bytes(self.stack.data[ro.addr - base : ro.addr - base + 8], "little")
        cur_fp = int.from_bytes(self.stack.data[fo.addr - base : fo.addr - base + 8], "little")
        caller = self.frames[rec.parent].func if rec.parent is not None else None
        if cur_ret != rec.ret_addr:
            where = self.code_description(cur_ret)
            now = f"which points to {where}" if where else "which is not an address in the program's code"
            self.warn(
                "stack-smashing",
                f"stack smashing: the return address in {rec.func}()'s frame was overwritten. It should lead back to "
                f"{self.code_description(rec.ret_addr)}, but now holds 0x{cur_ret:x}, {now}",
                at,
                hint="A local array in this function was written past its end. On a real system, returning would now make the CPU "
                "jump to that address: the program would crash, or gcc's stack protector would stop it with "
                "'*** stack smashing detected ***'. stacksim warns and continues as if the return address were intact.",
                addrs=[(ro.addr, 8)],
            )
        if cur_fp != rec.saved_fp:
            self.warn(
                "stack-smashing",
                f"stack smashing: the saved frame pointer in {rec.func}()'s frame was overwritten (0x{rec.saved_fp:x} became 0x{cur_fp:x})",
                at,
                hint=(f"After the return, {caller}() would use this wrong value as its frame pointer and could no longer find its own variables. " if caller else "")
                + "stacksim warns and continues as if it were intact.",
                addrs=[(fo.addr, 8)],
            )

    def frame_limit(self, fd, node=None) -> None:
        self.fatal(
            "stack-overflow",
            f"stack overflow: calling {fd.name}() would need more than {self.max_frames} stack frames",
            node if node is not None else fd.body,
            hint="This usually means endless recursion: a function that keeps calling itself without reaching its base case. "
            f"To keep the picture readable, stacksim allows at most {self.max_frames} nested calls (Run > Frame limit). "
            "A real stack has room for thousands of frames, but it runs out the same way.",
        )

    def stack_overflow(self, fd, node=None) -> None:
        depth = (self.frame.depth + 1) if self.frame else 0
        self.fatal(
            "stack-overflow",
            f"stack overflow: the stack is full after {depth} nested function calls (while calling {fd.name}())",
            node if node is not None else fd.body,
            hint="This usually means endless recursion: a function that keeps calling itself without reaching its base case. "
            f"(The simulated stack holds {STACK_SIZE // 1024} KiB; real systems typically allow 8 MiB.)",
        )

    def pop_frame(self, quiet: bool = False) -> None:
        rec = self.frame
        names = []
        while rec.blocks:
            _, oids = rec.blocks.pop()
            for oid in oids:
                self.kill(oid, "return")
                names.append(self.objects[oid].name)
        if not quiet:
            self.check_frame_slots(rec)
            # Returning reads the saved frame pointer and the return address.
            self.note_read(self.objects[rec.fp_obj].addr, 16)
        self.kill(rec.ret_obj, "return")
        self.kill(rec.fp_obj, "return")
        rec.death = len(self.steps)
        self.sp = rec.base
        self.frame = self.frames[rec.parent] if rec.parent is not None else None
        if not quiet:
            if names:
                self.event(f"The stack frame of {rec.func}() is removed - {', '.join(names)} no longer exist{'s' if len(names) == 1 else ''}.")
            else:
                self.event(f"The stack frame of {rec.func}() is removed.")

    def alloc_var(self, sym: Symbol, ty: T.CType, kind: str, name: str, line: int | None) -> tuple[int, int]:
        size = ty.size
        align = max(1, ty.align)
        addr = (self.sp - size) // align * align
        if addr < self.stack.base:
            self.stack_overflow(self.frame.fd, self.frame.fd.body if self.frame.fd else None)
        self.sp = addr
        rec = self.frame
        oid = self.new_object(kind, name, ty, addr, size, line, self.stack)
        self.objects[oid].frame = rec.id
        rec.vars.append(oid)
        rec.blocks[-1][1].append(oid)
        rec.var_map[sym] = (addr, oid)
        return addr, oid

    def push_block(self) -> None:
        self.frame.blocks.append([self.sp, []])

    def pop_block(self, announce: bool = True) -> None:
        rec = self.frame
        sp, oids = rec.blocks.pop()
        if oids:
            for oid in oids:
                self.kill(oid, "scope")
            if announce:
                names = [self.objects[o].name for o in oids]
                self.event(f"End of block: {', '.join(names)} {'no longer exists' if len(names) == 1 else 'no longer exist'}.")
        self.sp = sp

    def bind_params(self, fd: A.FuncDef, args: list, arg_tys: list) -> None:
        for p, v, at in zip(fd.params, args, arg_tys):
            if at is not None and at != p.ty and not isinstance(p.ty, T.StructType):
                v = self.convert(v, at, p.ty, p)
            addr, oid = self.alloc_var(p.sym, p.ty, "param", p.name, p.loc.line)
            self.store(addr, p.ty, v, oid, p)

    def call_function(self, fd: A.FuncDef, args: list, arg_tys: list, node) -> object:
        caller = self.frame
        call_line = node.loc.line if node is not None else None
        self.push_frame(fd, call_line, node)
        self.bind_params(fd, args, arg_tys)
        if fd.params:
            parts = ", ".join(f"{p.name} = {fmt_value(v, p.ty)}" for p, v in zip(fd.params, args))
            self.event(f"{caller.func}() calls {fd.name}(): a new stack frame is created. The parameters get copies of the arguments: {parts}.")
        else:
            self.event(f"{caller.func}() calls {fd.name}(): a new stack frame is created.")
        self.snap(fd.loc.line, "call")
        returned = self.run_body(fd)
        rec = self.frame
        if not returned:
            ret_ty = fd.ty.ret
            if not T.is_void(ret_ty):
                self.warn(
                    "missing-return",
                    f"{fd.name}() ended without a return statement, so its result is garbage",
                    fd.body,
                    hint=f"Add 'return ...;' so that {fd.name}() always returns {T.a_type(ret_ty)}.",
                )
                rec.ret_value = self.garbage_value(ret_ty)
            self.event(f"{fd.name}() reached its closing '}}' and returns.")
            self.snap(fd.body.end_loc.line, "end")
        ret = rec.ret_value
        self.pop_frame()
        return ret

    def garbage_value(self, ty: T.CType):
        if isinstance(ty, T.IntType):
            return ty.wrap(int.from_bytes(self.mem.garbage(ty.size), "little"))
        if isinstance(ty, T.FloatType):
            return 0.0
        if isinstance(ty, T.PointerType):
            return Ptr(int.from_bytes(self.mem.garbage(8), "little"))
        if isinstance(ty, T.StructType):
            return Blob(self.mem.garbage(ty.size), zeros(ty.size))
        return None

    def run_body(self, fd: A.FuncDef) -> bool:
        ex = self.exec
        for item in fd.body.items:
            if ex(item) == RETURN:
                return True
        return False

    # ========================================================= statements

    def exec(self, s) -> int:
        cls = s.__class__
        if cls is not A.Compound and cls is not A.Empty and cls is not A.Case and cls is not A.DeclStmt:
            self.snap(s.loc.line, "stmt")
        return self._ex[cls](s)

    def ex_Empty(self, s) -> int:
        return NORMAL

    def ex_ExprStmt(self, s) -> int:
        self.rv(s.expr)
        return NORMAL

    def ex_DeclStmt(self, s, snap: bool = True) -> int:
        decls = [d for d in s.decls if isinstance(d, A.VarDecl) and d.sym is not None and d.sym.storage == "local"]
        if not decls:
            return NORMAL
        if snap:
            self.snap(s.loc.line, "stmt")
        for d in decls:
            ty = d.ty
            addr, oid = self.alloc_var(d.sym, ty, "local", d.name, d.name_loc.line)
            if d.zero_fill:
                self.mem.write(self.stack, addr, zeros(ty.size))
            else:
                self.mem.mark_uninit(self.stack, addr, ty.size)
            self.clear_shadow(addr, ty.size)
            for off, ety, expr in d.init_plan:
                self.init_write(addr + off, ety, expr, oid)
            self.describe_new_var(d, addr)
        return NORMAL

    def describe_new_var(self, d: A.VarDecl, addr: int) -> None:
        ty = d.ty
        where = f"on the stack at 0x{addr:x} ({ty.size} byte{'s' if ty.size != 1 else ''})"
        tname = T.type_str(ty)
        if d.init is None:
            self.event(f"New variable {d.name} ({tname}) {where}. It has no initial value, so it contains leftover garbage.")
            return
        if T.is_scalar(ty) and d.init_plan:
            v = self.load_quiet(addr, ty)
            self.event(f"New variable {d.name} ({tname}) {where}, set to {fmt_value(v, ty)}.")
        else:
            self.event(f"New variable {d.name} ({tname}) {where}, filled from its initializer.")

    def load_quiet(self, addr: int, ty: T.CType):
        data, _ = self.mem.read(addr, ty.size)
        return decode(ty, data)

    def init_write(self, addr: int, ty: T.CType, expr, prov: int) -> None:
        if isinstance(expr, A.StrLit) and isinstance(ty, T.ArrayType):
            raw = expr.data + b"\0"
            raw = raw[: ty.length]
            r = self.access(addr, len(raw), prov, True, expr)
            self.mem.write(r, addr, raw)
            return
        v = self.rv(expr)
        self.store(addr, ty, v, prov, expr)

    def ex_Compound(self, s) -> int:
        self.push_block()
        ex = self.exec
        for item in s.items:
            code = ex(item)
            if code:
                self.pop_block()
                return code
        self.pop_block()
        return NORMAL

    def cond_event(self, cond, value: bool, then: str, other: str) -> None:
        self.event(f"The condition {expr_str(cond)} is {'true' if value else 'false'} - {then if value else other}.")

    def ex_If(self, s) -> int:
        c = truthy(self.rv(s.cond))
        self.cond_event(s.cond, c, "run the if-branch", "run the else-branch" if s.other is not None else "skip the if-branch")
        if c:
            return self.exec(s.then)
        if s.other is not None:
            return self.exec(s.other)
        return NORMAL

    def ex_While(self, s) -> int:
        while True:
            c = truthy(self.rv(s.cond))
            self.cond_event(s.cond, c, "run the loop body", "the loop ends")
            if not c:
                return NORMAL
            code = self.exec(s.body)
            if code == BREAK:
                self.event("break: leave the loop.")
                return NORMAL
            if code == RETURN:
                return RETURN
            self.snap(s.loc.line, "loop")

    def ex_DoWhile(self, s) -> int:
        while True:
            code = self.exec(s.body)
            if code == BREAK:
                self.event("break: leave the loop.")
                return NORMAL
            if code == RETURN:
                return RETURN
            self.snap(s.while_loc.line, "loop")
            c = truthy(self.rv(s.cond))
            self.cond_event(s.cond, c, "repeat the loop body", "the loop ends")
            if not c:
                return NORMAL

    def ex_For(self, s) -> int:
        self.push_block()
        init = s.init
        if init is not None:
            if isinstance(init, A.DeclStmt):
                self.ex_DeclStmt(init, snap=False)
            else:
                self.rv(init.expr)
        while True:
            if s.cond is not None:
                c = truthy(self.rv(s.cond))
                self.cond_event(s.cond, c, "run the loop body", "the loop ends")
                if not c:
                    break
            code = self.exec(s.body)
            if code == BREAK:
                self.event("break: leave the loop.")
                break
            if code == RETURN:
                self.pop_block(announce=False)
                return RETURN
            self.snap(s.loc.line, "loop")
            if s.step is not None:
                self.rv(s.step)
        self.pop_block()
        return NORMAL

    def ex_Switch(self, s) -> int:
        v = self.rv(s.expr)
        idx = s.table.get(v, s.default_index)
        if idx is None:
            self.event(f"switch: no case matches the value {v} - nothing to do.")
            return NORMAL
        label = f"case {v}" if v in s.table else "default"
        self.event(f"switch: the value is {v} - jump to '{label}'.")
        self.push_block()
        ex = self.exec
        for item in s.body.items[idx:]:
            code = ex(item)
            if code == BREAK:
                break
            if code:
                self.pop_block()
                return code
        self.pop_block()
        return NORMAL

    def ex_Case(self, s) -> int:
        return self.exec(s.stmt)

    def ex_Break(self, s) -> int:
        return BREAK

    def ex_Continue(self, s) -> int:
        self.event("continue: jump to the next round of the loop.")
        return CONTINUE

    def ex_Return(self, s) -> int:
        rec = self.frame
        if s.expr is not None:
            v = self.rv(s.expr)
            ty = s.expr.ty
            rec.ret_value = v
            rec.ret_text = fmt_value(v, ty) if not T.is_void(ty) else None
        else:
            rec.ret_value = None
            rec.ret_text = None
        if rec.ret_text is not None:
            self.event(f"{rec.func}() returns {rec.ret_text}.")
        else:
            self.event(f"{rec.func}() returns.")
        self.snap(s.loc.line, "return")
        rec.ret_step = len(self.steps) - 1
        return RETURN

    # ======================================================== expressions

    def rv(self, e):
        return self._rv[e.__class__](e)

    def lv(self, e) -> tuple[int, int | None]:
        return self._lv[e.__class__](e)

    def rv_IntLit(self, e):
        return e.value

    def rv_FloatLit(self, e):
        return e.value

    def var_location(self, e: A.Ident) -> tuple[int, int | None]:
        sym = e.sym
        st = sym.storage
        if st == "local" or st == "param":
            loc = self.frame.var_map.get(sym)
            if loc is None:
                self.fatal(
                    "internal",
                    f"'{e.name}' is used before its declaration was executed",
                    e,
                    hint="This happens when a switch jumps over a declaration. Declare variables before the switch.",
                )
            return loc
        return sym.addr, sym.obj

    def lv_Ident(self, e):
        return self.var_location(e)

    def rv_Ident(self, e):
        addr, prov = self.var_location(e)
        return self.load(addr, e.ty, prov, e)

    def rv_Decay(self, e):
        inner = e.expr
        if isinstance(inner.ty, T.FunctionType):
            if isinstance(inner, A.Ident) and inner.sym.kind == "func":
                return Ptr(self.func_addr[inner.name], None)
            addr, prov = self.lv(inner)
            return Ptr(addr, None)
        addr, prov = self.lv(inner)
        return Ptr(addr, prov)

    def lv_StrLit(self, e):
        return self.literal_ref[e.index]

    def lv_Call(self, e):
        # A struct returned from a function: materialize it in a temporary.
        v = self.rv(e)
        return self.temp(e.ty, v)

    def lv_temp(self, e):
        v = self.rv(e)
        return self.temp(e.ty, v)

    def temp(self, ty: T.CType, v) -> tuple[int, int]:
        rec = self.frame
        sym = Symbol("<temp>", "var", ty, "local", None)
        addr, oid = self.alloc_var(sym, ty, "local", "(temporary)", None)
        self.objects[oid].hidden = True
        self.store(addr, ty, v, oid, None)
        rec.var_map.pop(sym, None)
        return addr, oid

    def rv_Cast(self, e):
        inner = e.expr
        return self.convert(self.rv(inner), inner.ty, e.ty, e)

    def convert(self, v, src: T.CType, dst: T.CType, node):
        dc = dst.__class__
        if dc is T.IntType or dc is T.EnumType:
            if dst.rank == 0:
                return 1 if truthy(v) else 0
            sc = src.__class__
            if sc is T.IntType or sc is T.EnumType:
                return dst.wrap(v)
            if sc is T.FloatType:
                if v != v or v in (math.inf, -math.inf):
                    self.warn("float-conversion", f"converting {v} to {T.type_str(dst)} has no meaningful result", node)
                    return dst.min if dst.signed else 0
                iv = int(v)
                if iv < dst.min or iv > dst.max:
                    self.warn("float-conversion", f"the value {v:g} does not fit into {T.type_str(dst)}", node, hint=f"{T.type_str(dst)} can hold values from {dst.min} to {dst.max}.")
                    return dst.wrap(iv)
                return iv
            if sc is T.PointerType:
                return dst.wrap(v.addr)
            return dst.wrap(int(v))
        if dc is T.FloatType:
            f = float(v) if not isinstance(v, float) else v
            return float32(f) if dst.size == 4 else f
        if dc is T.PointerType:
            sc = src.__class__
            if sc is T.PointerType:
                if v.prov is not None and T.is_void(src.target) and not T.is_void(dst.target) and dst.target.complete:
                    o = self.objects[v.prov]
                    if o.kind == "heap" and o.hint is None and o.addr == v.addr:
                        o.hint = dst.target
                        o.hint_step = len(self.steps)
                return v
            if sc is T.IntType or sc is T.EnumType:
                return Ptr(v & MASK64, None)
            return v
        if dc is T.VoidType:
            return None
        return v

    def rv_Unary(self, e):
        op = e.op
        if op == "*":
            p = self.rv(e.operand)
            ty = e.ty
            if isinstance(ty, T.FunctionType):
                return p
            return self.load(p.addr, ty, p.prov, e)
        if op == "&":
            inner = e.operand
            if isinstance(inner, A.Ident) and inner.sym.kind == "func":
                return Ptr(self.func_addr[inner.name], None)
            addr, prov = self.lv(inner)
            return Ptr(addr, prov)
        if op == "++" or op == "--":
            addr, prov = self.lv(e.operand)
            ty = e.ty
            old = self.load(addr, ty, prov, e)
            new = self.step_value(old, ty, op, e)
            self.store(addr, ty, new, prov, e)
            self.event(f"{expr_str(e.operand)} {'increases' if op == '++' else 'decreases'} to {fmt_value(new, ty)}.")
            return new
        v = self.rv(e.operand)
        ty = e.ty
        if op == "-":
            if ty.__class__ is T.FloatType:
                return -v
            r = -v
            w = ty.wrap(r)
            if w != r and ty.signed:
                self.overflow(e, r, ty)
            return w
        if op == "+":
            return v
        if op == "!":
            return 0 if truthy(v) else 1
        if op == "~":
            return ty.wrap(~v)
        self.fatal("internal", f"unknown operator {op}", e)

    def step_value(self, old, ty: T.CType, op: str, e):
        d = 1 if op == "++" else -1
        if ty.__class__ is T.PointerType:
            return Ptr((old.addr + d * ty.target.size) & MASK64, old.prov)
        if ty.__class__ is T.FloatType:
            r = old + d
            return float32(r) if ty.size == 4 else r
        r = old + d
        w = ty.wrap(r)
        if w != r and ty.signed:
            self.overflow(e, r, ty)
        return w

    def rv_Postfix(self, e):
        addr, prov = self.lv(e.operand)
        ty = e.ty
        old = self.load(addr, ty, prov, e)
        new = self.step_value(old, ty, e.op, e)
        self.store(addr, ty, new, prov, e)
        self.event(f"{expr_str(e.operand)} {'increases' if e.op == '++' else 'decreases'} to {fmt_value(new, ty)}.")
        return old

    def overflow(self, e, r: int, ty: T.IntType) -> None:
        self.warn(
            "overflow",
            f"integer overflow: the result {r} does not fit into {T.type_str(ty)} and wraps around to {ty.wrap(r)}",
            e,
            hint=f"{T.type_str(ty)} can hold values from {ty.min} to {ty.max}. Use a larger type such as long long.",
        )

    def rv_Binary(self, e):
        mode = e.mode
        if mode == "logic":
            a = truthy(self.rv(e.left))
            if e.op == "&&":
                if not a:
                    return 0
                return 1 if truthy(self.rv(e.right)) else 0
            if a:
                return 1
            return 1 if truthy(self.rv(e.right)) else 0
        a = self.rv(e.left)
        b = self.rv(e.right)
        if mode == "cmp":
            op = e.op
            if op == "<":
                return 1 if a < b else 0
            if op == ">":
                return 1 if a > b else 0
            if op == "<=":
                return 1 if a <= b else 0
            if op == ">=":
                return 1 if a >= b else 0
            if op == "==":
                return 1 if a == b else 0
            return 1 if a != b else 0
        if mode == "arith" or mode == "shift":
            return self.arith(e.op, a, b, e.ty, e, mode)
        if mode == "ptr_add":
            return Ptr((a.addr + b * e.ty.target.size) & MASK64, a.prov)
        if mode == "ptr_sub":
            return Ptr((a.addr - b * e.ty.target.size) & MASK64, a.prov)
        if mode == "ptr_diff":
            size = e.left.ty.target.size
            d = a.addr - b.addr
            q = abs(d) // size
            return q if d >= 0 else -q
        if mode == "ptr_cmp":
            x, y = a.addr, b.addr
            op = e.op
            if op == "==":
                return 1 if x == y else 0
            if op == "!=":
                return 1 if x != y else 0
            if op == "<":
                return 1 if x < y else 0
            if op == ">":
                return 1 if x > y else 0
            if op == "<=":
                return 1 if x <= y else 0
            return 1 if x >= y else 0
        self.fatal("internal", f"unknown expression mode {mode}", e)

    def arith(self, op: str, a, b, ty: T.CType, e, mode: str = "arith"):
        if ty.__class__ is T.FloatType:
            if op == "+":
                r = a + b
            elif op == "-":
                r = a - b
            elif op == "*":
                r = a * b
            elif op == "/":
                if b == 0:
                    if a == 0 or a != a:
                        r = math.nan
                    else:
                        r = math.copysign(math.inf, a) * (math.copysign(1.0, b))
                else:
                    r = a / b
            else:
                self.fatal("internal", f"operator {op} on floating point", e)
            return float32(r) if ty.size == 4 else r
        if mode == "shift":
            bits = ty.bits
            if b < 0 or b >= bits:
                self.warn("shift", f"shifting by {b} bits is not defined for a {bits}-bit {T.type_str(ty)}", e)
                b %= bits
            if op == "<<":
                r = a << b
                w = ty.wrap(r)
                if ty.signed and w != r:
                    self.overflow(e, r, ty)
                return w
            return ty.wrap(a >> b)
        if op == "+":
            r = a + b
        elif op == "-":
            r = a - b
        elif op == "*":
            r = a * b
        elif op == "/" or op == "%":
            if b == 0:
                self.fatal(
                    "division-by-zero",
                    f"division by zero ({expr_str(e)} with a divisor of 0)" if hasattr(e, "left") else "division by zero",
                    e,
                    hint="Dividing an integer by zero crashes the program. Check the divisor before dividing.",
                )
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                q = -q
            r = q if op == "/" else a - b * q
        elif op == "&":
            r = a & b
        elif op == "|":
            r = a | b
        elif op == "^":
            r = a ^ b
        else:
            self.fatal("internal", f"unknown operator {op}", e)
        w = ty.wrap(r)
        if w != r and ty.signed:
            self.overflow(e, r, ty)
        return w

    def rv_Assign(self, e):
        target = e.target
        ty = e.ty
        addr, prov = self.lv(target)
        if e.op == "=":
            v = self.rv(e.value)
            self.store(addr, ty, v, prov, e)
            if ty.__class__ is T.StructType:
                self.event(f"{expr_str(target)} = {expr_str(e.value)} (all {ty.size} bytes are copied).")
            else:
                self.event(f"{expr_str(target)} = {fmt_value(v, ty)}")
            return v
        old = self.load(addr, ty, prov, e)
        rhs = self.rv(e.value)
        mode = e.mode
        if mode == "ptr_add":
            new = Ptr((old.addr + rhs * ty.target.size) & MASK64, old.prov)
        elif mode == "ptr_sub":
            new = Ptr((old.addr - rhs * ty.target.size) & MASK64, old.prov)
        else:
            opt = e.op_ty
            a = self.convert(old, ty, opt, e)
            r = self.arith(e.op[:-1], a, rhs, opt, e, mode)
            new = self.convert(r, opt, ty, e)
        self.store(addr, ty, new, prov, e)
        self.event(f"{expr_str(target)} {e.op} {expr_str(e.value)}  ->  {expr_str(target)} = {fmt_value(new, ty)}")
        return new

    def rv_Cond(self, e):
        if truthy(self.rv(e.cond)):
            return self.rv(e.then)
        return self.rv(e.other)

    def rv_Comma(self, e):
        self.rv(e.left)
        return self.rv(e.right)

    def rv_Index(self, e):
        addr, prov = self.lv_Index(e)
        return self.load(addr, e.ty, prov, e)

    def lv_Index(self, e):
        p = self.rv(e.base)
        i = self.rv(e.index)
        return (p.addr + i * e.ty.size) & MASK64, p.prov

    def lv_Unary(self, e):
        if e.op != "*":
            return self.lv_temp(e)
        p = self.rv(e.operand)
        return p.addr, p.prov

    def lv_Member(self, e):
        f = e.field
        if e.arrow:
            p = self.rv(e.obj)
            return (p.addr + f.offset) & MASK64, p.prov
        obj = e.obj
        if obj.__class__ in (A.Call, A.Assign, A.Cond, A.Comma):
            return self.lv_temp_member(e)
        addr, prov = self.lv(obj)
        return addr + f.offset, prov

    def lv_temp_member(self, e):
        addr, prov = self.lv(e.obj)
        return addr + e.field.offset, prov

    def rv_Member(self, e):
        f = e.field
        if not e.arrow and e.obj.__class__ in (A.Call, A.Assign, A.Cond, A.Comma):
            blob = self.rv(e.obj)
            data = blob.data[f.offset : f.offset + f.type.size]
            if isinstance(f.type, T.StructType):
                return Blob(data, blob.init[f.offset : f.offset + f.type.size], {k - f.offset: v for k, v in blob.ptrs.items() if f.offset <= k < f.offset + f.type.size})
            v = decode(f.type, data)
            if isinstance(f.type, T.PointerType):
                v.prov = blob.ptrs.get(f.offset)
            return v
        addr, prov = self.lv_Member(e)
        return self.load(addr, e.ty, prov, e)

    def rv_Call(self, e):
        fsym = e.fsym
        if fsym is not None and fsym.builtin:
            args = [self.rv(a) for a in e.args]
            impl = self.builtins.get(fsym.name)
            if impl is None:
                self.fatal("unsupported", f"the library function {fsym.name}() is not implemented", e)
            return impl(self, e, args)
        if fsym is not None:
            fd = self.prog.functions[fsym.name]
        else:
            p = self.rv(e.func)
            fd = self.code_map.get(p.addr)
            if fd is None:
                if p.addr == 0:
                    self.fatal("null-pointer", "calling a function through a NULL function pointer", e)
                self.fatal("segfault", f"calling a function through a pointer (0x{p.addr:x}) that does not point to a function", e)
        args = [self.rv(a) for a in e.args]
        return self.call_function(fd, args, [a.ty for a in e.args], e)


def execute(
    program: Program,
    stdin: bytes = b"",
    max_steps: int = DEFAULT_MAX_STEPS,
    cancel: threading.Event | None = None,
    source: str = "",
    max_frames: int = DEFAULT_MAX_FRAMES,
    input_provider=None,
    stdin_eof: bool = False,
    args: list[str] | None = None,
    prog_name: str = "./prog",
) -> Trace:
    """Run ``program`` in a thread with a large stack (deep C recursion needs deep Python recursion)."""
    result: dict = {}

    def target():
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, 400_000))
        try:
            result["trace"] = VM(program, stdin, max_steps, cancel, source, max_frames, input_provider, stdin_eof, args, prog_name).run()
        except BaseException as exc:  # pragma: no cover - surfaced to the caller
            result["error"] = exc

    old_size = threading.stack_size()
    try:
        threading.stack_size(512 * 1024 * 1024)
    except (ValueError, RuntimeError):  # pragma: no cover
        pass
    try:
        t = threading.Thread(target=target, name="stacksim-vm", daemon=True)
        t.start()
    finally:
        threading.stack_size(old_size)
    t.join()
    if "error" in result:
        raise result["error"]
    return result["trace"]

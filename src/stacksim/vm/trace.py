"""The recording of a program run: steps, memory objects and stack frames.

A trace is append-only. Objects and frames carry the step at which they appear
(``birth``) and disappear (``death``), so the state at any step can be
reconstructed without storing snapshots: memory bytes are rebuilt by applying
the per-step write logs forwards or backwards (see ``Cursor``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..c.ctype import CType
from ..c.errors import Diagnostic
from .memory import Memory


class MemObject:
    """A region of memory with a meaning: a variable, a heap block, a literal..."""

    __slots__ = (
        "id", "kind", "name", "ty", "addr", "size", "birth", "death", "frame", "line",
        "free_line", "death_reason", "hint", "hint_step", "hidden", "region", "chunk", "label", "owner",
    )

    def __init__(self, id: int, kind: str, name: str, ty: CType | None, addr: int, size: int, birth: int, line: int | None):
        self.id = id
        self.kind = kind  # local param global static literal heap frame builtin
        self.name = name
        self.ty = ty
        self.addr = addr
        self.size = size
        self.birth = birth
        self.death: int | None = None
        self.frame: int | None = None
        self.line = line  # declaration / allocation line
        self.free_line: int | None = None
        self.death_reason: str | None = None  # freed realloc scope return
        self.hint: CType | None = None  # element type of heap blocks, learned from casts
        self.hint_step = 0
        self.hidden = False
        self.region = None
        self.chunk = 0  # heap: size of the allocator chunk (header included)
        self.label = ""  # heap: "block #3"
        self.owner: str | None = None  # static locals: function name

    def alive_at(self, step: int) -> bool:
        return self.birth <= step and (self.death is None or step < self.death)

    @property
    def end(self) -> int:
        return self.addr + self.size

    def describe(self) -> str:
        if self.kind == "heap":
            return f"the heap {self.label} ({self.size} bytes, allocated at line {self.line})"
        if self.kind in ("local", "param"):
            return f"variable '{self.name}'"
        if self.kind in ("global", "static"):
            return f"{'static' if self.kind == 'static' else 'global'} variable '{self.name}'"
        if self.kind == "literal":
            return "a string literal"
        if self.kind == "retaddr":
            return f"the return address stored in {self.name}()'s stack frame"
        if self.kind == "savedfp":
            return f"the saved frame pointer in {self.name}()'s stack frame"
        return self.name


class FrameRec:
    __slots__ = (
        "id", "func", "parent", "depth", "call_line", "birth", "death", "vars", "ret_obj", "fp_obj",
        "ret_addr", "saved_fp", "ret_text", "ret_step", "base", "blocks", "var_map", "ret_value", "fd",
    )

    def __init__(self, id: int, func: str, parent: int | None, depth: int, call_line: int | None, birth: int, base: int):
        self.id = id
        self.func = func
        self.parent = parent
        self.depth = depth
        self.call_line = call_line
        self.birth = birth
        self.death: int | None = None
        self.vars: list[int] = []  # object ids, in declaration order
        # The bookkeeping slots at the top of the frame (x86-64 style):
        #   base - 8:  return address (pushed by the call)
        #   base - 16: saved frame pointer of the caller; this address is the frame's own frame pointer
        self.ret_obj: int | None = None
        self.fp_obj: int | None = None
        self.ret_addr = 0  # the value the call stored
        self.saved_fp = 0  # the value the prologue stored
        self.ret_text: str | None = None
        self.ret_step: int | None = None
        self.base = base  # stack pointer at frame entry
        # runtime only
        self.blocks: list = []
        self.var_map: dict = {}
        self.ret_value = None
        self.fd = None


class Step:
    __slots__ = ("line", "kind", "frame", "delta", "events", "out_len", "stdin_pos", "diags", "sp")

    def __init__(self, line, kind, frame, delta, events, out_len, stdin_pos, diags, sp):
        self.line = line  # the line that is about to execute (None at the end)
        self.kind = kind  # start stmt loop call return end exit error
        self.frame = frame  # id of the innermost frame (None after main returned)
        self.delta = delta  # memory writes since the previous step
        self.events = events  # explanations of what happened since the previous step
        self.out_len = out_len  # number of output chunks written so far
        self.stdin_pos = stdin_pos
        self.diags = diags  # indices into Trace.diagnostics raised since the previous step
        self.sp = sp


@dataclass
class Trace:
    source: str
    stdin: bytes
    steps: list[Step]
    objects: list[MemObject]
    frames: list[FrameRec]
    output: list[tuple[str, bytes]]
    diagnostics: list[Diagnostic]
    memory: Memory
    exit_code: int | None = None
    end_reason: str = "exit"  # exit error limit cancelled
    fatal: Diagnostic | None = None
    compile_diagnostics: list[Diagnostic] = field(default_factory=list)
    heap_base: int = 0
    code_names: dict[int, str] = field(default_factory=dict)  # function address -> name
    code_ranges: list[tuple[int, int, str]] = field(default_factory=list)  # (start, end, function)
    return_sites: dict[int, tuple[str, int | None]] = field(default_factory=dict)  # return address -> (description, line)
    runtime_return: int = 0  # where main() returns to (inside the C runtime)
    final_shadow: dict[int, int] = field(default_factory=dict)  # pointer provenance at the end

    def frame_chain(self, step: int) -> list[FrameRec]:
        """Frames alive at ``step``, outermost (main) first."""
        fid = self.steps[step].frame
        chain = []
        while fid is not None:
            fr = self.frames[fid]
            chain.append(fr)
            fid = fr.parent
        chain.reverse()
        return chain

    def describe_code(self, addr: int) -> str | None:
        """What a code address means: 'main(), line 12', 'the C runtime', or None if it is no code."""
        site = self.return_sites.get(addr)
        if site is not None:
            return site[0]
        for start, end, name in self.code_ranges:
            if start <= addr < end:
                return f"somewhere inside {name}()"
        return None

    def output_at(self, step: int) -> list[tuple[str, bytes]]:
        return self.output[: self.steps[step].out_len]


class Cursor:
    """Moves a Memory image to the state at any step by replaying write logs."""

    def __init__(self, trace: Trace):
        self.trace = trace
        self.mem = trace.memory
        # Which object each stored pointer was derived from, by address.
        self.prov: dict[int, int] = dict(trace.final_shadow)
        self.step = len(trace.steps) - 1  # the memory is in its final state after a run

    def goto(self, target: int) -> None:
        steps = self.trace.steps
        target = max(0, min(target, len(steps) - 1))
        apply = self.mem.apply
        prov = self.prov
        while self.step < target:
            self.step += 1
            for entry in steps[self.step].delta:
                if len(entry) == 3:
                    k, _, new = entry
                    if new is None:
                        prov.pop(k, None)
                    else:
                        prov[k] = new
                else:
                    apply(entry, True)
        while self.step > target:
            for entry in reversed(steps[self.step].delta):
                if len(entry) == 3:
                    k, old, _ = entry
                    if old is None:
                        prov.pop(k, None)
                    else:
                        prov[k] = old
                else:
                    apply(entry, False)
            self.step -= 1

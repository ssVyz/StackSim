"""Python implementations of the supported C standard library functions.

Every function receives the VM, the call node and the evaluated arguments.
Memory is always accessed through the VM so that bounds, use-after-free and
NULL problems are detected and explained like in user code.
"""

from __future__ import annotations

import math
import re

from ..c import ctype as T
from ..c.ast import expr_str
from .cformat import format_printf, scan
from .heap import HEADER
from .memory import HEAP_LIMIT, MASK64, zeros
from .values import Ptr, char_repr

EOF = -1


class _Exit(Exception):
    pass


def as_ptr(v) -> Ptr:
    if type(v) is Ptr:
        return v
    return Ptr(int(v) & MASK64)


def _null_check(vm, p: Ptr, node, func: str, what: str = "pointer") -> None:
    if p.addr == 0:
        vm.fatal(
            "null-pointer",
            f"{func}() was given a NULL {what}",
            node,
            hint="NULL points to nothing, so there is no data to read or write. Check pointers (for example the result of malloc) before passing them on.",
        )


def read_cstring(vm, p, node, func: str, limit: int | None = None) -> bytes:
    """Read a NUL-terminated string (at most ``limit`` bytes) with all safety checks."""
    p = as_ptr(p)
    _null_check(vm, p, node, func, "string pointer")
    addr = p.addr
    r = vm.access(addr, 1, p.prov, False, node)
    off = addr - r.base
    data = r.data
    stop = len(data) if limit is None else min(len(data), off + limit)
    k = data.find(0, off, stop)
    if k < 0:
        if limit is not None and off + limit <= len(data):
            k = off + limit
        else:
            vm.fatal(
                "segfault",
                f"{func}() read a string that never ends: there is no '\\0' before the end of the memory region",
                node,
                hint="Every C string must end with the character '\\0'. Without it, functions like printf and strlen keep reading.",
            )
    o = vm.objects[p.prov] if p.prov is not None else None
    if o is not None and o.death is None and o.addr <= addr < o.addr + o.size:
        obj_end = o.addr + o.size - r.base
        if k >= obj_end:
            vm.warn(
                "unterminated-string",
                f"{func}(): the string in {o.describe()} has no terminating '\\0' - reading continued past its end",
                node,
                hint="Every C string ends with '\\0'. Make arrays one character longer than the text, and make sure the '\\0' is copied.",
                addrs=[(o.addr, o.size)],
            )
    end_init = min(k + 1, len(data))
    if 0 in r.init[off:end_init]:
        bad = off + r.init[off:end_init].index(0)
        vm.uninit_read(r.base + bad, 1, p.prov if o is not None and o.addr <= r.base + bad < o.addr + o.size else None, node)
    return bytes(data[off:k])


def read_bytes(vm, p, n: int, node, func: str) -> tuple[bytes, bytes]:
    p = as_ptr(p)
    if n == 0:
        return b"", b""
    _null_check(vm, p, node, func)
    addr = p.addr
    o = vm.objects[p.prov] if p.prov is not None else None
    if o is not None and o.death is None and o.addr <= addr < o.addr + o.size and addr + n > o.addr + o.size:
        vm.warn(
            "out-of-bounds",
            f"out of bounds: {func}() reads {n} bytes from {o.describe()}, but only {o.addr + o.size - addr} are there",
            node,
            hint="The size given to the function is larger than the memory it reads from.",
            addrs=[(addr, n)],
        )
        r = vm.access_slow(addr, n, p.prov, False, node, quiet=True)
    else:
        r = vm.access(addr, n, p.prov, False, node)
    off = addr - r.base
    return bytes(r.data[off : off + n]), bytes(r.init[off : off + n])


def write_bytes(vm, p, data: bytes, node, func: str, init: bytes | None = None) -> None:
    p = as_ptr(p)
    n = len(data)
    if n == 0:
        return
    _null_check(vm, p, node, func, "destination pointer")
    addr = p.addr
    o = vm.objects[p.prov] if p.prov is not None else None
    if o is not None and o.death is None and o.addr <= addr < o.addr + o.size and addr + n > o.addr + o.size:
        room = o.addr + o.size - addr
        vm.warn(
            "buffer-overflow",
            f"buffer overflow: {func}() writes {n} bytes into {o.describe()}, but only {room} fit - {n - room} byte{'s' if n - room != 1 else ''} spill over into the memory behind it",
            node,
            hint="Make the destination large enough (remember the '\\0' at the end of strings), or use a function that limits the length, like snprintf or strncpy.",
            addrs=[(o.addr + o.size, n - room)],
        )
        r = vm.access_slow(addr, n, p.prov, True, node, quiet=True)
    else:
        r = vm.access(addr, n, p.prov, True, node)
    vm.mem.write(r, addr, data, init)
    vm.clear_shadow(addr, n)


def arg_text(node, i: int) -> str:
    try:
        return expr_str(node.args[i])
    except Exception:  # pragma: no cover
        return "?"


# ===================================================================== heap


def do_malloc(vm, n: int, node, func: str = "malloc") -> Ptr:
    if n > HEAP_LIMIT:
        hint = None
        if n > (1 << 62):
            hint = "The size is gigantic - most likely a negative number was converted to size_t (which is unsigned)."
        vm.event(f"{func}({n}) failed: there is not enough heap memory, so it returned NULL.")
        if hint:
            vm.warn("malloc-failed", f"{func}() was asked for {n} bytes and returned NULL", node, hint=hint)
        return Ptr(0)
    res = vm.heap.allocate(n)
    if res is None:
        vm.event(f"{func}({n}) failed: the heap is full ({HEAP_LIMIT // (1024 * 1024)} MiB), so it returned NULL.")
        return Ptr(0)
    user, csize = res
    vm.heap_counter += 1
    line = node.loc.line if node is not None else None
    oid = vm.new_object("heap", f"block #{vm.heap_counter}", None, user, n, line, vm.heap.region)
    o = vm.objects[oid]
    o.chunk = csize
    o.label = f"block #{vm.heap_counter}"
    vm.heap.live[user] = oid
    vm.clear_shadow(user - HEADER, csize)
    return Ptr(user, oid)


def do_free(vm, p, node) -> None:
    p = as_ptr(p)
    what = arg_text(node, 0)
    if p.addr == 0:
        vm.event(f"free({what}): the pointer is NULL, so free() does nothing.")
        return
    prov = vm.objects[p.prov] if p.prov is not None else None
    if prov is not None and prov.kind == "heap" and prov.death is not None and prov.addr == p.addr:
        vm.warn(
            "double-free",
            f"double free: {prov.label} was already freed at line {prov.free_line}",
            node,
            hint="Each block must be freed exactly once. Setting the pointer to NULL after free() prevents this (free(NULL) does nothing).",
            addrs=[(p.addr, max(1, prov.size))],
        )
        return
    oid = vm.heap.live.get(p.addr)
    if oid is None:
        if prov is not None and prov.kind == "heap" and prov.death is None:
            msg = f"free() was given an address inside {prov.label} ({p.addr - prov.addr} bytes after its start), not the start of the block"
            hint = "Pass free() exactly the pointer that malloc returned. If you moved the pointer (p++), keep a copy of the original."
        elif prov is not None and prov.kind in ("local", "param", "global", "static", "literal"):
            where = "the stack" if prov.kind in ("local", "param") else "static memory"
            msg = f"free() was given the address of {prov.describe()}, which lives in {where}, not on the heap"
            hint = "Only memory that came from malloc, calloc, realloc or strdup may be freed. Other variables are cleaned up automatically."
        else:
            msg = f"free() was given the address 0x{p.addr:x}, which was not returned by malloc"
            hint = "Only pointers returned by malloc, calloc, realloc or strdup may be freed - each exactly once."
        vm.warn("invalid-free", msg + " - the call is ignored", node, hint=hint)
        return
    o = vm.objects[oid]
    if not vm.heap.header_ok(o.addr, o.chunk):
        vm.warn(
            "heap-corruption",
            f"heap corruption: the bookkeeping data in front of {o.label} was overwritten",
            node,
            hint="This happens when a program writes past the end of the previous heap block. A real malloc would usually abort here.",
            addrs=[(o.addr - HEADER, HEADER)],
        )
    vm.heap.release(o.addr, o.chunk)
    del vm.heap.live[o.addr]
    vm.kill(oid, "freed")
    o.free_line = node.loc.line if node is not None else None
    vm.event(
        f"free({what}) gives {o.label} ({o.size} bytes at 0x{o.addr:x}) back to the heap. "
        f"{what} still holds the old address - it is now a dangling pointer."
    )


def b_malloc(vm, node, args):
    n = args[0]
    p = do_malloc(vm, n, node)
    if p.addr:
        vm.event(f"malloc({n}) reserves {n} bytes on the heap ({vm.objects[p.prov].label} at 0x{p.addr:x}) and returns its address. The memory is not initialized.")
    return p


def b_calloc(vm, node, args):
    count, size = args
    n = count * size
    if n > T.SIZE_T.max:
        vm.event("calloc(): the total size overflows, so it returned NULL.")
        return Ptr(0)
    p = do_malloc(vm, n, node, "calloc")
    if p.addr and n:
        vm.mem.write(vm.heap.region, p.addr, zeros(n))
    if p.addr:
        vm.event(f"calloc({count}, {size}) reserves {n} bytes on the heap ({vm.objects[p.prov].label} at 0x{p.addr:x}) and sets them all to 0.")
    return p


def b_realloc(vm, node, args):
    p, n = as_ptr(args[0]), args[1]
    if p.addr == 0:
        q = do_malloc(vm, n, node, "realloc")
        if q.addr:
            vm.event(f"realloc(NULL, {n}) works like malloc({n}): new {vm.objects[q.prov].label} at 0x{q.addr:x}.")
        return q
    oid = vm.heap.live.get(p.addr)
    if oid is None:
        prov = vm.objects[p.prov] if p.prov is not None else None
        if prov is not None and prov.kind == "heap" and prov.death is not None:
            vm.warn("use-after-free", f"realloc() was given {prov.label}, which was already freed at line {prov.free_line}", node)
        else:
            vm.warn("invalid-free", "realloc() was given a pointer that did not come from malloc", node, hint="realloc() only works with pointers returned by malloc, calloc or realloc.")
        return Ptr(0)
    old = vm.objects[oid]
    if n == 0:
        do_free(vm, p, node)
        return Ptr(0)
    line = node.loc.line if node is not None else None
    new_chunk = vm.heap.try_grow_in_place(old.addr, old.chunk, n)
    if new_chunk is not None:
        vm.heap_counter += 1
        nid = vm.new_object("heap", f"block #{vm.heap_counter}", None, old.addr, n, line, vm.heap.region)
        no = vm.objects[nid]
        no.chunk = new_chunk
        no.label = f"block #{vm.heap_counter}"
        no.hint, no.hint_step = old.hint, len(vm.steps)
        vm.kill(oid, "realloc")
        old.free_line = line
        vm.heap.live[old.addr] = nid
        if n > old.size:
            vm.mem.mark_uninit(vm.heap.region, old.addr + old.size, n - old.size)
        # Resized in place: pointers to the old block stay valid in practice.
        for k, v in list(vm.shadow.items()):
            if v == oid:
                vm.sh_set(k, nid)
        vm.event(f"realloc() resizes {old.label} in place from {old.size} to {n} bytes (now {no.label}); its address 0x{old.addr:x} stays the same.")
        return Ptr(old.addr, nid)
    q = do_malloc(vm, n, node, "realloc")
    if q.addr == 0:
        vm.event("realloc() failed and returned NULL; the old block is unchanged.")
        return q
    keep = min(n, old.size)
    r = vm.heap.region
    off = old.addr - r.base
    data, init = bytes(r.data[off : off + keep]), bytes(r.init[off : off + keep])
    vm.mem.write(r, q.addr, data, init)
    vm.copy_shadow(q.addr, old.addr, keep)
    no = vm.objects[q.prov]
    no.hint, no.hint_step = old.hint, len(vm.steps)
    vm.heap.release(old.addr, old.chunk)
    del vm.heap.live[old.addr]
    vm.kill(oid, "realloc")
    old.free_line = line
    vm.event(
        f"realloc() could not grow {old.label} in place: it allocated {no.label} ({n} bytes at 0x{q.addr:x}), "
        f"copied {keep} bytes over and freed the old block. Pointers to the old block are now dangling."
    )
    return q


def b_free(vm, node, args):
    do_free(vm, args[0], node)


def b_exit(vm, node, args):
    from .interpreter import ProgramExit

    vm.event(f"exit({args[0]}) ends the program immediately.")
    raise ProgramExit(T.INT.wrap(args[0]))


def b_abort(vm, node, args):
    vm.write_out("stderr", b"Aborted\n")
    vm.fatal("abort", "the program called abort()", node)


def b_assert(vm, node, args):
    cond, text, line = args
    if cond:
        return None
    expr = read_cstring(vm, text, node, "assert")
    msg = f"main.c:{line}: Assertion `{expr.decode('utf-8', 'replace')}' failed.\n"
    vm.write_out("stderr", msg.encode())
    vm.fatal("assert", f"assertion failed: {expr.decode('utf-8', 'replace')}", node, hint="assert() stops the program when its condition is false.")


# ==================================================================== stdio


def _stream(vm, v, node, func) -> str:
    p = as_ptr(v)
    _null_check(vm, p, node, func, "FILE pointer")
    s = vm.file_streams.get(p.addr)
    if s is None:
        vm.fatal("invalid-file", f"{func}() was given a pointer that is not a FILE", node, hint="Only stdin, stdout and stderr are available in stacksim.")
    return s


def _emit(vm, stream: str, data: bytes, func: str, node) -> None:
    if stream == "stdin":
        vm.warn("invalid-file", f"{func}() cannot write to stdin", node)
        return
    vm.write_out(stream, data)
    shown = data.decode("utf-8", "replace")
    if len(shown) > 60:
        shown = shown[:57] + "..."
    shown = shown.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
    vm.event(f'{func}() prints "{shown}"' + (" to stderr" if stream == "stderr" else "") + ".")


def _fmt(vm, node, args, fmt_index: int, func: str) -> bytes:
    fmt = read_cstring(vm, args[fmt_index], node, func)
    rest = args[fmt_index + 1 :]
    types = [a.ty for a in node.args[fmt_index + 1 :]]
    return format_printf(vm, node, fmt, rest, types, func)


def b_printf(vm, node, args):
    out = _fmt(vm, node, args, 0, "printf")
    _emit(vm, "stdout", out, "printf", node)
    return len(out)


def b_fprintf(vm, node, args):
    stream = _stream(vm, args[0], node, "fprintf")
    out = _fmt(vm, node, args, 1, "fprintf")
    _emit(vm, stream, out, "fprintf", node)
    return len(out)


def b_sprintf(vm, node, args):
    out = _fmt(vm, node, args, 1, "sprintf")
    write_bytes(vm, args[0], out + b"\0", node, "sprintf")
    vm.event(f'sprintf() writes "{out.decode("utf-8", "replace")}" into {arg_text(node, 0)}.')
    return len(out)


def b_snprintf(vm, node, args):
    out = _fmt(vm, node, args, 2, "snprintf")
    size = args[1]
    if size > 0:
        data = out[: size - 1] + b"\0"
        write_bytes(vm, args[0], data, node, "snprintf")
        vm.event(f'snprintf() writes "{data[:-1].decode("utf-8", "replace")}" into {arg_text(node, 0)}' + (" (shortened to fit)" if len(out) >= size else "") + ".")
    return len(out)


def b_puts(vm, node, args):
    s = read_cstring(vm, args[0], node, "puts")
    _emit(vm, "stdout", s + b"\n", "puts", node)
    return 1


def b_putchar(vm, node, args):
    c = args[0] & 0xFF
    _emit(vm, "stdout", bytes([c]), "putchar", node)
    return c


def b_fputs(vm, node, args):
    stream = _stream(vm, args[1], node, "fputs")
    s = read_cstring(vm, args[0], node, "fputs")
    _emit(vm, stream, s, "fputs", node)
    return 1


def b_fputc(vm, node, args):
    stream = _stream(vm, args[1], node, "fputc")
    c = args[0] & 0xFF
    _emit(vm, stream, bytes([c]), "fputc", node)
    return c


def b_fflush(vm, node, args):
    return 0


def b_perror(vm, node, args):
    p = as_ptr(args[0])
    prefix = read_cstring(vm, p, node, "perror") + b": " if p.addr else b""
    _emit(vm, "stderr", prefix + b"Error\n", "perror", node)


def b_scanf(vm, node, args):
    fmt = read_cstring(vm, args[0], node, "scanf")
    count, pos = scan(vm, node, vm.stdin, vm.stdin_pos, fmt, args[1:], node.args[1:], "scanf")
    vm.stdin_pos = pos
    return count


def b_sscanf(vm, node, args):
    src = read_cstring(vm, args[0], node, "sscanf")
    fmt = read_cstring(vm, args[1], node, "sscanf")
    count, _ = scan(vm, node, src, 0, fmt, args[2:], node.args[2:], "sscanf")
    return count


def b_getchar(vm, node, args):
    if vm.stdin_pos >= len(vm.stdin):
        vm.event("getchar() reached the end of the input and returns EOF (-1).")
        return EOF
    c = vm.stdin[vm.stdin_pos]
    vm.stdin_pos += 1
    vm.event(f"getchar() reads {char_repr(c)} from the input.")
    return c


def b_fgetc(vm, node, args):
    stream = _stream(vm, args[0], node, "fgetc")
    if stream != "stdin":
        vm.warn("invalid-file", "fgetc() can only read from stdin", node)
        return EOF
    return b_getchar(vm, node, [])


def b_fgets(vm, node, args):
    buf, size = as_ptr(args[0]), args[1]
    stream = _stream(vm, args[2], node, "fgets")
    if stream != "stdin":
        vm.warn("invalid-file", "fgets() can only read from stdin", node)
        return Ptr(0)
    if vm.stdin_pos >= len(vm.stdin) or size <= 0:
        vm.event("fgets() reached the end of the input and returns NULL.")
        return Ptr(0)
    data = bytearray()
    while len(data) < size - 1 and vm.stdin_pos < len(vm.stdin):
        c = vm.stdin[vm.stdin_pos]
        vm.stdin_pos += 1
        data.append(c)
        if c == 10:
            break
    write_bytes(vm, buf, bytes(data) + b"\0", node, "fgets")
    shown = data.decode("utf-8", "replace").replace("\n", "\\n")
    vm.event(f'fgets() reads "{shown}" into {arg_text(node, 0)}.')
    return buf


# =================================================================== stdlib


def b_abs(vm, node, args):
    return T.INT.wrap(abs(args[0]))


def b_labs(vm, node, args):
    return T.LONG.wrap(abs(args[0]))


_NUM_PREFIX = re.compile(rb"\s*([+-]?\d+)")
_FLOAT_PREFIX = re.compile(rb"\s*([+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan))", re.I)


def b_atoi(vm, node, args):
    s = read_cstring(vm, args[0], node, "atoi")
    m = _NUM_PREFIX.match(s)
    return T.INT.wrap(int(m.group(1))) if m else 0


def b_atol(vm, node, args):
    s = read_cstring(vm, args[0], node, "atol")
    m = _NUM_PREFIX.match(s)
    return T.LONG.wrap(int(m.group(1))) if m else 0


def b_atof(vm, node, args):
    s = read_cstring(vm, args[0], node, "atof")
    m = _FLOAT_PREFIX.match(s)
    return float(m.group(1)) if m else 0.0


def _store_endptr(vm, node, endp, base_ptr: Ptr, consumed: int) -> None:
    endp = as_ptr(endp)
    if endp.addr:
        vm.store(endp.addr, T.CHAR_PTR, Ptr(base_ptr.addr + consumed, base_ptr.prov), endp.prov, node)


def b_strtol(vm, node, args):
    p = as_ptr(args[0])
    s = read_cstring(vm, p, node, "strtol")
    base = args[2]
    i = 0
    while i < len(s) and s[i] in b" \t\n\r\f\v":
        i += 1
    sign = 1
    if i < len(s) and s[i] in b"+-":
        sign = -1 if s[i] == 45 else 1
        i += 1
    if base in (0, 16) and s[i : i + 2].lower() == b"0x":
        i += 2
        base = 16
    elif base == 0:
        base = 8 if s[i : i + 1] == b"0" else 10
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"[: max(2, min(36, base))]
    start = i
    val = 0
    while i < len(s) and chr(s[i]).lower() in digits:
        val = val * base + digits.index(chr(s[i]).lower())
        i += 1
    if i == start:
        i = 0
    _store_endptr(vm, node, args[1], p, i)
    return max(T.LONG.min, min(T.LONG.max, sign * val))


def b_strtod(vm, node, args):
    p = as_ptr(args[0])
    s = read_cstring(vm, p, node, "strtod")
    m = _FLOAT_PREFIX.match(s)
    if not m:
        _store_endptr(vm, node, args[1], p, 0)
        return 0.0
    _store_endptr(vm, node, args[1], p, m.end())
    return float(m.group(1))


def _glibc_rand_init(seed: int) -> dict:
    seed &= 0xFFFFFFFF
    if seed == 0:
        seed = 1
    r = [0] * 34
    r[0] = seed if seed < 0x80000000 else seed - (1 << 32)
    for i in range(1, 31):
        hi, lo = divmod(r[i - 1], 127773) if r[i - 1] >= 0 else (-((-r[i - 1]) // 127773), -((-r[i - 1]) % 127773))
        word = 16807 * lo - 2836 * hi
        if word < 0:
            word += 2147483647
        r[i] = word
    for i in range(31, 34):
        r[i] = r[i - 31]
    r = [x & 0xFFFFFFFF for x in r]
    state = {"r": r}
    for _ in range(34, 344):
        _glibc_next(state)
    return state


def _glibc_next(state: dict) -> int:
    r = state["r"]
    v = (r[-31] + r[-3]) & 0xFFFFFFFF
    r.append(v)
    if len(r) > 64:
        del r[:-34]
    return v >> 1


def b_rand(vm, node, args):
    if vm.rand_state is None:
        vm.rand_state = _glibc_rand_init(1)
    v = _glibc_next(vm.rand_state)
    return v


def b_srand(vm, node, args):
    vm.rand_state = _glibc_rand_init(args[0])
    vm.event(f"srand({args[0]}) sets the start value of the random number generator.")


def b_qsort(vm, node, args):
    base, count, size, cmp = as_ptr(args[0]), args[1], args[2], as_ptr(args[3])
    fd = vm.code_map.get(cmp.addr)
    if fd is None:
        vm.fatal("segfault", "qsort() was given a comparison function pointer that does not point to a function", node)
    if count <= 1 or size == 0:
        return None
    read_bytes(vm, base, count * size, node, "qsort")
    vm.event(f"qsort() sorts {count} elements of {size} bytes, calling {fd.name}() to compare two elements at a time.")
    r = vm.access(base.addr, count * size, base.prov, True, node)
    vp = T.PointerType(T.VOID)

    def compare(i: int, j: int) -> int:
        a = Ptr(base.addr + i * size, base.prov)
        b = Ptr(base.addr + j * size, base.prov)
        res = vm.call_function(fd, [a, b], [vp, vp], node)
        return res if isinstance(res, int) else 0

    for i in range(1, count):
        lo, hi = 0, i
        while lo < hi:
            mid = (lo + hi) // 2
            if compare(mid, i) > 0:
                hi = mid
            else:
                lo = mid + 1
        if lo < i:
            off = base.addr - r.base
            elem = bytes(r.data[off + i * size : off + (i + 1) * size])
            elem_i = bytes(r.init[off + i * size : off + (i + 1) * size])
            moved = bytes(r.data[off + lo * size : off + i * size])
            moved_i = bytes(r.init[off + lo * size : off + i * size])
            vm.mem.write(r, base.addr + lo * size, elem + moved, elem_i + moved_i)
            vm.clear_shadow(base.addr + lo * size, (i - lo + 1) * size)
    return None


# =================================================================== string


def b_strlen(vm, node, args):
    return len(read_cstring(vm, args[0], node, "strlen"))


def b_strcpy(vm, node, args):
    s = read_cstring(vm, args[1], node, "strcpy")
    write_bytes(vm, args[0], s + b"\0", node, "strcpy")
    vm.event(f'strcpy() copies "{s.decode("utf-8", "replace")}" ({len(s) + 1} bytes including \'\\0\') into {arg_text(node, 0)}.')
    return as_ptr(args[0])


def b_strncpy(vm, node, args):
    n = args[2]
    s = read_cstring(vm, args[1], node, "strncpy", limit=n)
    data = (s + bytes(n))[:n]
    write_bytes(vm, args[0], data, node, "strncpy")
    if len(s) >= n:
        vm.event(f"strncpy() copied {n} characters into {arg_text(node, 0)} without a terminating '\\0' (the source was too long).")
    return as_ptr(args[0])


def b_strcat(vm, node, args):
    d = as_ptr(args[0])
    cur = read_cstring(vm, d, node, "strcat")
    s = read_cstring(vm, args[1], node, "strcat")
    write_bytes(vm, Ptr(d.addr + len(cur), d.prov), s + b"\0", node, "strcat")
    vm.event(f'strcat() appends "{s.decode("utf-8", "replace")}" to {arg_text(node, 0)}.')
    return d


def b_strncat(vm, node, args):
    d = as_ptr(args[0])
    cur = read_cstring(vm, d, node, "strncat")
    s = read_cstring(vm, args[1], node, "strncat", limit=args[2])
    write_bytes(vm, Ptr(d.addr + len(cur), d.prov), s + b"\0", node, "strncat")
    return d


def _cmp(a: bytes, b: bytes) -> int:
    for x, y in zip(a, b):
        if x != y:
            return x - y
    return len(a) - len(b) if len(a) != len(b) else 0


def b_strcmp(vm, node, args):
    a = read_cstring(vm, args[0], node, "strcmp")
    b = read_cstring(vm, args[1], node, "strcmp")
    r = _cmp(a + b"\0", b + b"\0")
    return max(-1, min(1, r))


def b_strncmp(vm, node, args):
    n = args[2]
    a = read_cstring(vm, args[0], node, "strncmp", limit=n)
    b = read_cstring(vm, args[1], node, "strncmp", limit=n)
    r = _cmp((a + b"\0")[:n], (b + b"\0")[:n])
    return max(-1, min(1, r))


def b_strchr(vm, node, args):
    p = as_ptr(args[0])
    s = read_cstring(vm, p, node, "strchr")
    c = args[1] & 0xFF
    i = (s + b"\0").find(bytes([c]))
    return Ptr(p.addr + i, p.prov) if i >= 0 else Ptr(0)


def b_strrchr(vm, node, args):
    p = as_ptr(args[0])
    s = read_cstring(vm, p, node, "strrchr")
    c = args[1] & 0xFF
    i = (s + b"\0").rfind(bytes([c]))
    return Ptr(p.addr + i, p.prov) if i >= 0 else Ptr(0)


def b_strstr(vm, node, args):
    p = as_ptr(args[0])
    s = read_cstring(vm, p, node, "strstr")
    needle = read_cstring(vm, args[1], node, "strstr")
    i = s.find(needle)
    return Ptr(p.addr + i, p.prov) if i >= 0 else Ptr(0)


def b_strdup(vm, node, args):
    s = read_cstring(vm, args[0], node, "strdup")
    q = do_malloc(vm, len(s) + 1, node, "strdup")
    if q.addr:
        o = vm.objects[q.prov]
        o.hint, o.hint_step = T.CHAR, len(vm.steps)
        vm.mem.write(vm.heap.region, q.addr, s + b"\0")
        vm.event(f'strdup() allocates {len(s) + 1} bytes on the heap ({o.label} at 0x{q.addr:x}) and copies "{s.decode("utf-8", "replace")}" into it. Remember to free() it.')
    return q


def b_memcpy(vm, node, args, func="memcpy"):
    dst, src, n = as_ptr(args[0]), as_ptr(args[1]), args[2]
    if n == 0:
        return dst
    data, init = read_bytes(vm, src, n, node, func)
    if func == "memcpy" and src.addr < dst.addr + n and dst.addr < src.addr + n and src.addr != dst.addr:
        vm.warn("overlap", "memcpy() with overlapping source and destination", node, hint="The result is undefined when the areas overlap. Use memmove() instead.")
    write_bytes(vm, dst, data, node, func, init)
    vm.copy_shadow(dst.addr, src.addr, n)
    vm.event(f"{func}() copies {n} bytes from {arg_text(node, 1)} to {arg_text(node, 0)}.")
    return dst


def b_memmove(vm, node, args):
    return b_memcpy(vm, node, args, "memmove")


def b_memset(vm, node, args):
    dst, c, n = as_ptr(args[0]), args[1] & 0xFF, args[2]
    if n:
        write_bytes(vm, dst, bytes([c]) * n, node, "memset")
        vm.event(f"memset() sets {n} bytes of {arg_text(node, 0)} to {c}.")
    return dst


def b_memcmp(vm, node, args):
    a, _ = read_bytes(vm, args[0], args[2], node, "memcmp")
    b, _ = read_bytes(vm, args[1], args[2], node, "memcmp")
    return max(-1, min(1, _cmp(a, b)))


# ==================================================================== ctype


def _ctype(pred):
    def f(vm, node, args):
        c = args[0]
        if c == EOF:
            return 0
        return 1 if pred(c & 0xFF) else 0

    return f


def b_toupper(vm, node, args):
    c = args[0]
    return c - 32 if 97 <= c <= 122 else c


def b_tolower(vm, node, args):
    c = args[0]
    return c + 32 if 65 <= c <= 90 else c


# ===================================================================== math


def _math1(fn):
    def f(vm, node, args):
        try:
            return float(fn(args[0]))
        except (ValueError, ZeroDivisionError):
            return math.nan
        except OverflowError:
            return math.inf

    return f


def _log(fn):
    def f(vm, node, args):
        x = args[0]
        if x == 0:
            return -math.inf
        if x < 0 or x != x:
            return math.nan
        return fn(x)

    return f


def b_pow(vm, node, args):
    try:
        r = math.pow(args[0], args[1])
    except ValueError:
        return math.nan
    except OverflowError:
        return math.inf
    return r


def b_fmod(vm, node, args):
    if args[1] == 0:
        return math.nan
    return math.fmod(args[0], args[1])


def b_round(vm, node, args):
    x = args[0]
    if x != x or x in (math.inf, -math.inf):
        return x
    return float(math.floor(x + 0.5)) if x >= 0 else -float(math.floor(-x + 0.5))


def b_time(vm, node, args):
    now = 1_700_000_000
    p = as_ptr(args[0])
    if p.addr:
        vm.store(p.addr, T.LONG, now, p.prov, node)
    return now


BUILTINS = {
    "malloc": b_malloc, "calloc": b_calloc, "realloc": b_realloc, "free": b_free,
    "exit": b_exit, "abort": b_abort, "__stacksim_assert": b_assert,
    "printf": b_printf, "fprintf": b_fprintf, "sprintf": b_sprintf, "snprintf": b_snprintf,
    "puts": b_puts, "putchar": b_putchar, "fputs": b_fputs, "fputc": b_fputc, "putc": b_fputc,
    "fflush": b_fflush, "perror": b_perror,
    "scanf": b_scanf, "sscanf": b_sscanf, "getchar": b_getchar, "fgetc": b_fgetc, "getc": b_fgetc, "fgets": b_fgets,
    "abs": b_abs, "labs": b_labs, "atoi": b_atoi, "atol": b_atol, "atof": b_atof, "strtol": b_strtol, "strtod": b_strtod,
    "rand": b_rand, "srand": b_srand, "qsort": b_qsort,
    "strlen": b_strlen, "strcpy": b_strcpy, "strncpy": b_strncpy, "strcat": b_strcat, "strncat": b_strncat,
    "strcmp": b_strcmp, "strncmp": b_strncmp, "strchr": b_strchr, "strrchr": b_strrchr, "strstr": b_strstr,
    "strdup": b_strdup, "memcpy": b_memcpy, "memmove": b_memmove, "memset": b_memset, "memcmp": b_memcmp,
    "isalpha": _ctype(lambda c: chr(c).isalpha() and c < 128), "isdigit": _ctype(lambda c: 48 <= c <= 57),
    "isalnum": _ctype(lambda c: c < 128 and chr(c).isalnum()), "isspace": _ctype(lambda c: c in b" \t\n\r\f\v"),
    "isupper": _ctype(lambda c: 65 <= c <= 90), "islower": _ctype(lambda c: 97 <= c <= 122),
    "ispunct": _ctype(lambda c: 33 <= c <= 126 and not chr(c).isalnum()), "isprint": _ctype(lambda c: 32 <= c <= 126),
    "isxdigit": _ctype(lambda c: chr(c) in "0123456789abcdefABCDEF"), "toupper": b_toupper, "tolower": b_tolower,
    "sqrt": _math1(math.sqrt), "pow": b_pow, "fabs": _math1(math.fabs), "floor": _math1(math.floor), "ceil": _math1(math.ceil),
    "round": b_round, "trunc": _math1(math.trunc), "fmod": b_fmod,
    "fmin": lambda vm, node, a: min(a[0], a[1]), "fmax": lambda vm, node, a: max(a[0], a[1]),
    "sin": _math1(math.sin), "cos": _math1(math.cos), "tan": _math1(math.tan), "atan": _math1(math.atan),
    "atan2": lambda vm, node, a: math.atan2(a[0], a[1]), "exp": _math1(math.exp),
    "log": _log(math.log), "log10": _log(math.log10), "log2": _log(math.log2),
    "hypot": lambda vm, node, a: math.hypot(a[0], a[1]),
    "time": b_time,
}

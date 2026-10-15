"""printf-style formatting and scanf-style parsing on byte strings."""

from __future__ import annotations

import re
import struct

from ..c import ctype as T
from .memory import MASK64
from .values import Ptr

_SPEC = re.compile(rb"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d*))?(hh|h|ll|l|z|j|t|L|q)?([a-zA-Z%])?")
_SCAN_SPEC = re.compile(rb"%(\*)?(\d+)?(hh|h|ll|l|z|j|t|L)?(\[\^?\]?[^\]]*\]|[a-zA-Z%])?")

_INT_BITS = {None: 32, b"hh": 8, b"h": 16, b"l": 64, b"ll": 64, b"q": 64, b"z": 64, b"j": 64, b"t": 64, b"L": 64}


def _wrap(v: int, bits: int, signed: bool) -> int:
    mask = (1 << bits) - 1
    v &= mask
    if signed and v >= 1 << (bits - 1):
        v -= 1 << bits
    return v


def _as_int(v) -> int:
    if type(v) is Ptr:
        return v.addr
    if isinstance(v, float):
        # A double passed where an int is expected: real printf would print
        # garbage from another register; show the low bits of the double.
        return struct.unpack("<q", struct.pack("<d", v))[0]
    return int(v) if v is not None else 0


def _as_float(v) -> float:
    if isinstance(v, float):
        return v
    return 0.0


def format_printf(vm, node, fmt: bytes, args: list, arg_types: list, func: str) -> bytes:
    out = bytearray()
    ai = 0
    i = 0
    n = len(fmt)
    missing_warned = False

    def next_arg():
        nonlocal ai, missing_warned
        if ai < len(args):
            v, t = args[ai], arg_types[ai]
            ai += 1
            return v, t
        ai += 1
        if not missing_warned:
            missing_warned = True
            vm.warn("format", f"{func}(): the format string needs more values than were passed - garbage is printed instead", node)
        return int.from_bytes(vm.mem.garbage(4), "little"), T.INT

    while i < n:
        j = fmt.find(b"%", i)
        if j < 0:
            out += fmt[i:]
            break
        out += fmt[i:j]
        m = _SPEC.match(fmt, j)
        flags, width, prec, length, conv = m.groups()
        i = m.end()
        if conv is None:
            out += fmt[j:i]
            continue
        if conv == b"%":
            out += b"%"
            continue
        if width == b"*":
            w, _ = next_arg()
            w = _as_int(w)
            if w < 0:
                flags += b"-"
                w = -w
            width = str(w).encode()
        if prec == b"*":
            p, _ = next_arg()
            p = _as_int(p)
            prec = str(p).encode() if p >= 0 else None
        spec = b"%" + flags + (width or b"") + (b"." + prec if prec is not None else b"")
        c = conv
        if c in (b"d", b"i"):
            v, _ = next_arg()
            out += (spec + b"d") % _wrap(_as_int(v), _INT_BITS[length], True)
        elif c in (b"u", b"x", b"X", b"o"):
            v, _ = next_arg()
            iv = _wrap(_as_int(v), _INT_BITS[length], False)
            if c == b"u":
                out += (spec + b"d") % iv
            elif c == b"o" and b"#" in flags:
                digits = b"%o" % iv
                if not digits.startswith(b"0"):
                    digits = b"0" + digits
                out += (b"%" + flags.replace(b"#", b"").replace(b"0", b"") + (width or b"") + b"s") % digits
            elif iv == 0 and b"#" in flags:
                out += (spec.replace(b"#", b"") + c) % iv
            else:
                out += (spec + c) % iv
        elif c == b"c":
            v, _ = next_arg()
            out += (b"%" + flags.replace(b"0", b"") + (width or b"") + b"c") % (_as_int(v) & 0xFF)
        elif c == b"s":
            v, t = next_arg()
            if type(v) is not Ptr:
                v = Ptr(_as_int(v) & MASK64)
            if v.addr == 0:
                s = b"(null)"
            else:
                limit = int(prec) if prec not in (None, b"") else (0 if prec == b"" else None)
                from .libc import read_cstring

                s = read_cstring(vm, v, node, func, limit=limit)
            out += (b"%" + flags.replace(b"0", b"") + (width or b"") + b"s") % s
        elif c in (b"f", b"F", b"e", b"E", b"g", b"G"):
            v, _ = next_arg()
            out += (spec + c) % _as_float(v)
        elif c in (b"a", b"A"):
            v, _ = next_arg()
            s = float.hex(_as_float(v)).encode()
            out += s.upper() if c == b"A" else s
        elif c == b"p":
            v, _ = next_arg()
            addr = _as_int(v) & MASK64
            s = b"(nil)" if addr == 0 else b"0x%x" % addr
            out += (b"%" + flags.replace(b"0", b"") + (width or b"") + b"s") % s
        elif c == b"n":
            next_arg()
        else:
            out += fmt[j:i]
    return bytes(out)


_WS = b" \t\n\r\f\v"
_INT_RE = {
    b"d": re.compile(rb"[+-]?\d+"),
    b"u": re.compile(rb"[+-]?\d+"),
    b"i": re.compile(rb"[+-]?(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9]\d*)"),
    b"o": re.compile(rb"[+-]?[0-7]+"),
    b"x": re.compile(rb"[+-]?(?:0[xX])?[0-9a-fA-F]+"),
    b"X": re.compile(rb"[+-]?(?:0[xX])?[0-9a-fA-F]+"),
    b"p": re.compile(rb"(?:0[xX])?[0-9a-fA-F]+"),
}
_FLOAT_RE = re.compile(rb"[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)", re.I)

_SIGNED = {None: T.INT, b"hh": T.SCHAR, b"h": T.SHORT, b"l": T.LONG, b"ll": T.LLONG, b"z": T.SIZE_T, b"j": T.LONG, b"t": T.LONG, b"L": T.LLONG}
_UNSIGNED = {None: T.UINT, b"hh": T.UCHAR, b"h": T.USHORT, b"l": T.ULONG, b"ll": T.ULLONG, b"z": T.SIZE_T, b"j": T.ULONG, b"t": T.ULONG, b"L": T.ULLONG}


def _int_value(text: bytes, conv: bytes) -> int:
    t = text.decode()
    if conv in (b"d", b"u"):
        return int(t, 10)
    if conv == b"o":
        return int(t, 8)
    if conv in (b"x", b"X", b"p"):
        return int(t, 16)
    # %i: base from prefix
    sign = -1 if t.startswith("-") else 1
    body = t.lstrip("+-")
    if body.lower().startswith("0x"):
        return sign * int(body, 16)
    if body.startswith("0") and len(body) > 1:
        return sign * int(body, 8)
    return sign * int(body, 10)


def _scanset(spec: bytes) -> tuple[set[int], bool]:
    inner = spec[1:-1]
    negate = inner.startswith(b"^")
    if negate:
        inner = inner[1:]
    chars: set[int] = set()
    k = 0
    while k < len(inner):
        if k + 2 < len(inner) and inner[k + 1] == ord("-"):
            chars.update(range(inner[k], inner[k + 2] + 1))
            k += 3
        else:
            chars.add(inner[k])
            k += 1
    return chars, negate


def scan(vm, node, inp: bytes, pos: int, fmt: bytes, args: list, arg_nodes: list, func: str) -> tuple[int, int]:
    """Parse ``inp`` from ``pos`` according to ``fmt``; store into the pointer ``args``.

    Returns (number of assigned conversions or -1 for EOF, new input position).
    """
    from ..c.ast import expr_str
    from .libc import as_ptr, write_bytes

    count = 0
    ai = 0
    i = 0
    n = len(inp)
    stored: list[str] = []

    def target(t_desc: str):
        nonlocal ai
        if ai >= len(args):
            vm.warn("format", f"{func}(): the format string has more conversions than there are arguments", node)
            return None, None
        p = as_ptr(args[ai])
        name = arg_nodes[ai]
        ai += 1
        return p, name

    def describe(node_arg) -> str:
        s = expr_str(node_arg)
        return s[1:] if s.startswith("&") else f"*{s}" if not s.startswith("*") else s

    while i < len(fmt):
        c = fmt[i]
        if c in _WS:
            while i < len(fmt) and fmt[i] in _WS:
                i += 1
            while pos < n and inp[pos] in _WS:
                pos += 1
            continue
        if c != 0x25:
            if pos < n and inp[pos] == c:
                pos += 1
                i += 1
                continue
            if pos >= n and count == 0:
                return -1, pos
            break
        m = _SCAN_SPEC.match(fmt, i)
        suppress, width, length, conv = m.groups()
        i = m.end()
        if conv is None:
            break
        if conv == b"%":
            while pos < n and inp[pos] in _WS:
                pos += 1
            if pos < n and inp[pos] == 0x25:
                pos += 1
                continue
            break
        if conv == b"n":
            if not suppress:
                p, _ = target("int")
                if p is not None:
                    vm.store(p.addr, T.INT, pos, p.prov, node)
            continue
        if conv not in (b"c",) and not conv.startswith(b"["):
            while pos < n and inp[pos] in _WS:
                pos += 1
        if pos >= n:
            return (count if count else -1), pos
        w = int(width) if width else None
        end = n if w is None else min(n, pos + w)
        if conv in _INT_RE:
            mm = _INT_RE[conv].match(inp, pos, end)
            if not mm:
                break
            pos = mm.end()
            value = _int_value(mm.group(), conv)
            if suppress:
                continue
            p, an = target("int")
            if p is None:
                break
            if conv == b"p":
                vm.store(p.addr, T.VOID_PTR, Ptr(value & MASK64), p.prov, node)
            else:
                ty = _SIGNED[length] if conv in (b"d", b"i") else _UNSIGNED[length]
                vm.store(p.addr, ty, ty.wrap(value), p.prov, node)
                stored.append(f"{value} into {describe(an)}")
            count += 1
        elif conv in (b"f", b"F", b"e", b"E", b"g", b"G", b"a", b"A"):
            mm = _FLOAT_RE.match(inp, pos, end)
            if not mm:
                break
            pos = mm.end()
            value = float(mm.group())
            if suppress:
                continue
            p, an = target("double")
            if p is None:
                break
            ty = T.DOUBLE if length in (b"l", b"L") else T.FLOAT
            vm.store(p.addr, ty, T.float32(value) if ty is T.FLOAT else value, p.prov, node)
            stored.append(f"{value:g} into {describe(an)}")
            count += 1
        elif conv == b"c":
            k = w or 1
            data = inp[pos : pos + k]
            if not data:
                break
            pos += len(data)
            if suppress:
                continue
            p, an = target("char")
            if p is None:
                break
            write_bytes(vm, p, data, node, func)
            stored.append(f"{data.decode('latin-1')!r} into {describe(an)}")
            count += 1
        elif conv == b"s" or conv.startswith(b"["):
            start = pos
            if conv == b"s":
                while pos < end and inp[pos] not in _WS:
                    pos += 1
            else:
                chars, negate = _scanset(conv)
                while pos < end and ((inp[pos] in chars) != negate):
                    pos += 1
            if pos == start:
                break
            data = inp[start:pos]
            if suppress:
                continue
            p, an = target("char *")
            if p is None:
                break
            write_bytes(vm, p, data + b"\0", node, func)
            stored.append(f'"{data.decode("utf-8", "replace")}" into {describe(an)}')
            count += 1
        else:
            vm.warn("format", f"{func}(): the conversion '%{conv.decode()}' is not supported", node)
            break
    if stored:
        vm.event(f"{func}() read " + ", ".join(stored) + ".")
    elif count == 0:
        vm.event(f"{func}() could not read a value (the input did not match the format).")
    return count, pos

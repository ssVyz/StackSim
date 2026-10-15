"""Runtime values and their byte encodings.

Integers and floats are plain Python numbers. Pointers are ``Ptr`` objects that
carry, besides the address, the id of the memory object they were derived from
("provenance"); that lets the VM say "this pointer belongs to array a, and you
are reading past its end" even if the address happens to hit another variable.
"""

from __future__ import annotations

import struct

from ..c import ctype as T
from .memory import MASK64


class Ptr:
    __slots__ = ("addr", "prov")

    def __init__(self, addr: int, prov: int | None = None):
        self.addr = addr
        self.prov = prov

    def __repr__(self) -> str:
        return f"Ptr(0x{self.addr:x}, prov={self.prov})"


class Blob:
    """A struct value: raw bytes, init flags and pointer provenance by offset."""

    __slots__ = ("data", "init", "ptrs")

    def __init__(self, data: bytes, init: bytes, ptrs: dict[int, int] | None = None):
        self.data = data
        self.init = init
        self.ptrs = ptrs or {}


_F32 = struct.Struct("<f")
_F64 = struct.Struct("<d")


def encode(ty: T.CType, v) -> bytes:
    if isinstance(ty, T.IntType):
        return (v & ty.mask).to_bytes(ty.size, "little")
    if isinstance(ty, T.PointerType):
        return (v.addr & MASK64).to_bytes(8, "little")
    if isinstance(ty, T.FloatType):
        if ty.size == 4:
            try:
                return _F32.pack(v)
            except OverflowError:
                return _F32.pack(float("inf") if v > 0 else float("-inf"))
        return _F64.pack(v)
    if isinstance(ty, T.StructType):
        return v.data
    raise TypeError(f"cannot encode {ty}")


def decode(ty: T.CType, data: bytes):
    if isinstance(ty, T.IntType):
        v = int.from_bytes(data, "little", signed=ty.signed)
        return v
    if isinstance(ty, T.PointerType):
        return Ptr(int.from_bytes(data, "little"))
    if isinstance(ty, T.FloatType):
        return (_F32 if ty.size == 4 else _F64).unpack(data)[0]
    raise TypeError(f"cannot decode {ty}")


def truthy(v) -> bool:
    if type(v) is Ptr:
        return v.addr != 0
    return v != 0


def fmt_float(v: float) -> str:
    if v != v:
        return "nan"
    if v in (float("inf"), float("-inf")):
        return "inf" if v > 0 else "-inf"
    s = f"{v:.6g}"
    if "e" not in s and "." not in s:
        s += ".0"
    return s


def char_repr(c: int) -> str:
    c &= 0xFF
    special = {0: "\\0", 10: "\\n", 9: "\\t", 13: "\\r", 39: "\\'", 92: "\\\\"}
    if c in special:
        return f"'{special[c]}'"
    if 32 <= c < 127:
        return f"'{chr(c)}'"
    return f"'\\x{c:02x}'"


def fmt_value(v, ty: T.CType) -> str:
    """A short human-readable rendering of a value (for explanations)."""
    if v is None:
        return "nothing"
    if isinstance(ty, T.PointerType):
        if v.addr == 0:
            return "NULL"
        return f"0x{v.addr:x}"
    if isinstance(ty, T.IntType):
        if ty.size == 1 and ty.rank == 1:
            return f"{v} ({char_repr(v)})"
        if ty.rank == 0:
            return "true" if v else "false"
        return str(v)
    if isinstance(ty, T.FloatType):
        return fmt_float(v)
    if isinstance(ty, T.StructType):
        return "{...}"
    return str(v)

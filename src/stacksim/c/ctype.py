"""The C type system used by the checker, the interpreter and the GUI.

The model follows a typical 64-bit Linux machine (LP64): ``int`` is 4 bytes,
``long`` and pointers are 8 bytes, values are stored little-endian and structs
get the usual alignment padding.
"""

from __future__ import annotations

import struct as _struct
from dataclasses import dataclass

POINTER_SIZE = 8


class CType:
    size: int = 0
    align: int = 1
    complete: bool = True

    def __str__(self) -> str:
        return type_str(self)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {type_str(self)}>"


class VoidType(CType):
    size = 1
    align = 1
    complete = False

    def __eq__(self, other):
        return isinstance(other, VoidType)

    def __hash__(self):
        return hash("void")


class IntType(CType):
    """An integer type. ``rank`` orders bool < char < short < int < long < long long."""

    def __init__(self, name: str, size: int, signed: bool, rank: int):
        self.name = name
        self.size = size
        self.align = size
        self.signed = signed
        self.rank = rank
        self.bits = size * 8
        self.mask = (1 << self.bits) - 1
        if signed:
            self.min = -(1 << (self.bits - 1))
            self.max = (1 << (self.bits - 1)) - 1
        else:
            self.min = 0
            self.max = self.mask

    def wrap(self, v: int) -> int:
        """Reduce ``v`` modulo 2^bits into this type's range (two's complement)."""
        if self.rank == 0:  # _Bool
            return 1 if v else 0
        v &= self.mask
        if self.signed and v > self.max:
            v -= 1 << self.bits
        return v

    def alias(self, name: str) -> "IntType":
        """The same type under a typedef name (e.g. size_t)."""
        t = IntType(name, self.size, self.signed, self.rank)
        return t

    def __eq__(self, other):
        return isinstance(other, IntType) and other.rank == self.rank and other.signed == self.signed

    def __hash__(self):
        return hash(("int", self.rank, self.signed))


class EnumType(IntType):
    def __init__(self, tag: str | None):
        super().__init__(f"enum {tag}" if tag else "enum", 4, True, 3)
        self.tag = tag


class FloatType(CType):
    def __init__(self, name: str, size: int):
        self.name = name
        self.size = size
        self.align = size

    def __eq__(self, other):
        return isinstance(other, FloatType) and other.size == self.size

    def __hash__(self):
        return hash(("float", self.size))


class PointerType(CType):
    size = POINTER_SIZE
    align = POINTER_SIZE

    def __init__(self, target: CType):
        self.target = target

    def __eq__(self, other):
        return isinstance(other, PointerType) and other.target == self.target

    def __hash__(self):
        return hash(("ptr", self.target))


class ArrayType(CType):
    def __init__(self, elem: CType, length: int | None):
        self.elem = elem
        self.length = length
        self.align = elem.align
        self.complete = length is not None
        self.size = elem.size * length if length is not None else 0

    def __eq__(self, other):
        return isinstance(other, ArrayType) and other.elem == self.elem and other.length == self.length

    def __hash__(self):
        return hash(("arr", self.elem, self.length))


@dataclass(slots=True)
class Field:
    name: str
    type: CType
    offset: int


class StructType(CType):
    """A struct or union. Compared by identity, like in C."""

    def __init__(self, tag: str | None, is_union: bool = False):
        self.tag = tag
        self.is_union = is_union
        self.fields: list[Field] = []
        self.by_name: dict[str, Field] = {}
        self.complete = False
        self.size = 0
        self.align = 1
        self.display_name: str | None = None  # typedef name for anonymous structs

    @property
    def keyword(self) -> str:
        return "union" if self.is_union else "struct"

    def define(self, members: list[tuple[str, CType]]) -> None:
        offset = 0
        align = 1
        size = 0
        for name, t in members:
            a = t.align
            align = max(align, a)
            if self.is_union:
                self.fields.append(Field(name, t, 0))
                size = max(size, t.size)
            else:
                offset = (offset + a - 1) // a * a
                self.fields.append(Field(name, t, offset))
                offset += t.size
                size = offset
        self.size = (size + align - 1) // align * align if size else 0
        self.align = align
        self.by_name = {f.name: f for f in self.fields}
        self.complete = True

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)


class FunctionType(CType):
    size = 1
    align = 1

    def __init__(self, ret: CType, params: list[CType], variadic: bool = False, prototyped: bool = True):
        self.ret = ret
        self.params = params
        self.variadic = variadic
        self.prototyped = prototyped

    def __eq__(self, other):
        return (
            isinstance(other, FunctionType)
            and other.ret == self.ret
            and other.params == self.params
            and other.variadic == self.variadic
        )

    def __hash__(self):
        return hash(("fn", self.ret, tuple(self.params)))


VOID = VoidType()
BOOL = IntType("bool", 1, False, 0)
CHAR = IntType("char", 1, True, 1)
SCHAR = IntType("signed char", 1, True, 1)
UCHAR = IntType("unsigned char", 1, False, 1)
SHORT = IntType("short", 2, True, 2)
USHORT = IntType("unsigned short", 2, False, 2)
INT = IntType("int", 4, True, 3)
UINT = IntType("unsigned int", 4, False, 3)
LONG = IntType("long", 8, True, 4)
ULONG = IntType("unsigned long", 8, False, 4)
LLONG = IntType("long long", 8, True, 5)
ULLONG = IntType("unsigned long long", 8, False, 5)
FLOAT = FloatType("float", 4)
DOUBLE = FloatType("double", 8)
LDOUBLE = FloatType("long double", 8)  # simplified: same as double

SIZE_T = ULONG.alias("size_t")
PTRDIFF_T = LONG.alias("ptrdiff_t")

CHAR_PTR = PointerType(CHAR)
VOID_PTR = PointerType(VOID)

_UNSIGNED_OF = {1: UCHAR, 2: USHORT, 3: UINT, 4: ULONG, 5: ULLONG}


def is_integer(t: CType) -> bool:
    return isinstance(t, IntType)


def is_float(t: CType) -> bool:
    return isinstance(t, FloatType)


def is_arith(t: CType) -> bool:
    return isinstance(t, (IntType, FloatType))


def is_pointer(t: CType) -> bool:
    return isinstance(t, PointerType)


def is_scalar(t: CType) -> bool:
    return isinstance(t, (IntType, FloatType, PointerType))


def is_aggregate(t: CType) -> bool:
    return isinstance(t, (ArrayType, StructType))


def is_void(t: CType) -> bool:
    return isinstance(t, VoidType)


def is_char_array(t: CType) -> bool:
    return isinstance(t, ArrayType) and isinstance(t.elem, IntType) and t.elem.size == 1


def decay(t: CType) -> CType:
    if isinstance(t, ArrayType):
        return PointerType(t.elem)
    if isinstance(t, FunctionType):
        return PointerType(t)
    return t


def promote(t: CType) -> CType:
    """Integer promotion: small integer types become int."""
    if isinstance(t, IntType) and t.rank < 3:
        return INT
    return t


def arith_common(a: CType, b: CType) -> CType:
    """The usual arithmetic conversions."""
    if isinstance(a, FloatType) or isinstance(b, FloatType):
        if isinstance(a, FloatType) and isinstance(b, FloatType):
            return a if a.size >= b.size else b
        return a if isinstance(a, FloatType) else b
    a = promote(a)
    b = promote(b)
    assert isinstance(a, IntType) and isinstance(b, IntType)
    if a == b:
        return a
    if a.signed == b.signed:
        return a if a.rank >= b.rank else b
    u, s = (a, b) if not a.signed else (b, a)
    if u.rank >= s.rank:
        return u
    if s.size > u.size:
        return s
    return _UNSIGNED_OF[s.rank]


def float32(v: float) -> float:
    """Round a Python float to single precision (inf on overflow)."""
    try:
        return _struct.unpack("<f", _struct.pack("<f", v))[0]
    except OverflowError:
        return float("inf") if v > 0 else float("-inf")


def a_type(t: CType) -> str:
    """The type name with an indefinite article: 'an int', 'a double'."""
    s = type_str(t)
    return ("an " if s[:1] in "aeiou" else "a ") + s


def type_str(t: CType, inner: str = "") -> str:
    """Format a type the way C declares it, e.g. ``int (*)[3]``."""
    if isinstance(t, PointerType):
        s = "*" + inner
        if isinstance(t.target, (ArrayType, FunctionType)):
            s = "(" + s + ")"
        return type_str(t.target, s)
    if isinstance(t, ArrayType):
        n = "" if t.length is None else str(t.length)
        return type_str(t.elem, f"{inner}[{n}]")
    if isinstance(t, FunctionType):
        if t.params:
            ps = ", ".join(type_str(p) for p in t.params)
            if t.variadic:
                ps += ", ..."
        else:
            ps = "..." if t.variadic else "void"
        return type_str(t.ret, f"{inner}({ps})")
    if isinstance(t, StructType):
        base = t.display_name or f"{t.keyword} {t.tag or '<anonymous>'}"
    elif isinstance(t, (IntType, FloatType)):
        base = t.name
    elif isinstance(t, VoidType):
        base = "void"
    else:
        base = "?"
    if not inner:
        return base
    if inner.startswith("["):
        return base + inner
    return f"{base} {inner}"

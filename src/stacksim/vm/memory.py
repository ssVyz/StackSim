"""Byte-addressed virtual memory with per-byte "initialized" flags.

Every write is logged as (addr, old_bytes, old_init, new_bytes, new_init) so
that a recorded run can be replayed forwards and backwards.

Address space layout (a simplified 64-bit Linux process):

    0x00000000  unmapped (NULL page)
    0x00401000  code      - functions (only their addresses are used)
    0x00402000  rodata    - string literals (read-only)
    0x00404000  data      - global and static variables
    0x00405000+ heap      - malloc/calloc/realloc, grows upwards
    0x7ffdf000  stack     - grows downwards from 0x7ffff000
"""

from __future__ import annotations

import random

CODE_BASE = 0x00401000
RODATA_BASE = 0x00402000
DATA_BASE = 0x00404000
HEAP_BASE_MIN = 0x00405000
HEAP_LIMIT = 16 * 1024 * 1024
STACK_TOP = 0x7FFFF000
STACK_SIZE = 128 * 1024
NULL_PAGE = 0x1000
MASK64 = (1 << 64) - 1

_ONES: dict[int, bytes] = {}
_ZEROS: dict[int, bytes] = {}


def ones(n: int) -> bytes:
    b = _ONES.get(n)
    if b is None:
        b = b"\x01" * n
        if n <= 4096:
            _ONES[n] = b
    return b


def zeros(n: int) -> bytes:
    b = _ZEROS.get(n)
    if b is None:
        b = bytes(n)
        if n <= 4096:
            _ZEROS[n] = b
    return b


class Region:
    __slots__ = ("name", "kind", "base", "data", "init", "readonly")

    def __init__(self, name: str, kind: str, base: int, data: bytearray, readonly: bool = False):
        self.name = name
        self.kind = kind
        self.base = base
        self.data = data
        self.init = bytearray(len(data))
        self.readonly = readonly

    @property
    def end(self) -> int:
        return self.base + len(self.data)

    def contains(self, addr: int, size: int = 1) -> bool:
        return self.base <= addr and addr + size <= self.base + len(self.data)


class Memory:
    def __init__(self, seed: int = 0x5EED):
        self.regions: list[Region] = []
        self.log: list = []
        self.rng = random.Random(seed)

    def garbage(self, n: int) -> bytes:
        return self.rng.randbytes(n)

    def add_region(self, name: str, kind: str, base: int, size: int, readonly: bool = False, fill: str = "garbage") -> Region:
        data = bytearray(self.garbage(size) if fill == "garbage" else bytes(size))
        r = Region(name, kind, base, data, readonly)
        self.regions.append(r)
        return r

    def region_at(self, addr: int, size: int = 1) -> Region | None:
        for r in self.regions:
            if r.base <= addr and addr + size <= r.base + len(r.data):
                return r
        return None

    def region_containing(self, addr: int) -> Region | None:
        """Like region_at, but for a single byte; used to classify addresses."""
        return self.region_at(addr, 1)

    # -- logged mutation ---------------------------------------------------

    def write(self, r: Region, addr: int, data: bytes, init: bytes | None = None) -> None:
        off = addr - r.base
        n = len(data)
        end = off + n
        old_d = bytes(r.data[off:end])
        old_i = bytes(r.init[off:end])
        r.data[off:end] = data
        ni = init if init is not None else ones(n)
        r.init[off:end] = ni
        self.log.append((addr, old_d, old_i, bytes(data), ni))

    def mark_uninit(self, r: Region, addr: int, n: int) -> None:
        off = addr - r.base
        end = off + n
        cur = bytes(r.data[off:end])
        old_i = bytes(r.init[off:end])
        z = zeros(n)
        r.init[off:end] = z
        self.log.append((addr, cur, old_i, cur, z))

    # -- unlogged access ---------------------------------------------------

    def read(self, addr: int, n: int) -> tuple[bytes, bytes]:
        r = self.region_at(addr, n)
        if r is None:
            return b"", b""
        off = addr - r.base
        return bytes(r.data[off : off + n]), bytes(r.init[off : off + n])

    def peek(self, addr: int, n: int) -> tuple[bytes, bytes] | None:
        """Read possibly-unmapped memory for display (None if not fully mapped)."""
        r = self.region_at(addr, n)
        if r is None:
            return None
        off = addr - r.base
        return bytes(r.data[off : off + n]), bytes(r.init[off : off + n])

    def apply(self, entry, forward: bool) -> None:
        addr, old_d, old_i, new_d, new_i = entry
        r = self.region_at(addr, len(old_d))
        if r is None:  # pragma: no cover - logs only contain mapped writes
            return
        off = addr - r.base
        n = len(old_d)
        if forward:
            r.data[off : off + n] = new_d
            r.init[off : off + n] = new_i
        else:
            r.data[off : off + n] = old_d
            r.init[off : off + n] = old_i

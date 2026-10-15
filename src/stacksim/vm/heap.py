"""A simple first-fit heap allocator.

Each block is preceded by a 16-byte header (like glibc's malloc): 8 bytes
"previous size" and 8 bytes holding the chunk size with the in-use bit set.
Freed chunks are merged with free neighbours and reused by later allocations,
so learners can watch a dangling pointer suddenly point into a *new* block.
"""

from __future__ import annotations

from .memory import HEAP_LIMIT, Memory, Region

HEADER = 16
ALIGN = 16
MIN_SPLIT = 32


def chunk_size_for(n: int) -> int:
    return HEADER + (max(n, 1) + ALIGN - 1) // ALIGN * ALIGN


class Heap:
    def __init__(self, mem: Memory, base: int):
        self.mem = mem
        self.base = base
        self.brk = base
        self.limit = base + HEAP_LIMIT
        self.region: Region = mem.add_region("heap", "heap", base, 0)
        self.free_chunks: list[list[int]] = []  # sorted [start, size]
        self.live: dict[int, int] = {}  # user address -> object id

    def _grow(self, new_brk: int) -> None:
        r = self.region
        extra = new_brk - r.end
        if extra > 0:
            r.data.extend(self.mem.garbage(extra))
            r.init.extend(bytes(extra))
        self.brk = new_brk

    def header_bytes(self, chunk_size: int) -> bytes:
        return bytes(8) + (chunk_size | 1).to_bytes(8, "little")

    def allocate(self, n: int) -> tuple[int, int] | None:
        """Reserve a chunk for ``n`` bytes. Returns (user address, chunk size) or None."""
        need = chunk_size_for(n)
        for i, (start, size) in enumerate(self.free_chunks):
            if size >= need:
                if size - need >= MIN_SPLIT:
                    self.free_chunks[i] = [start + need, size - need]
                    csize = need
                else:
                    del self.free_chunks[i]
                    csize = size
                return self._place(start, csize, n)
        # Extend the heap. A free chunk touching the top can be reused partially.
        if self.free_chunks and self.free_chunks[-1][0] + self.free_chunks[-1][1] == self.brk:
            start, size = self.free_chunks.pop()
        else:
            start, size = self.brk, 0
        if start + need > self.limit:
            if size:
                self.free_chunks.append([start, size])
            return None
        if start + need > self.brk:
            self._grow(start + need)
        return self._place(start, need, n)

    def _place(self, start: int, csize: int, n: int) -> tuple[int, int]:
        self.mem.write(self.region, start, self.header_bytes(csize))
        user = start + HEADER
        self.mem.mark_uninit(self.region, user, csize - HEADER)
        return user, csize

    def release(self, user: int, csize: int) -> None:
        start = user - HEADER
        # Clear the in-use bit in the header.
        self.mem.write(self.region, start + 8, csize.to_bytes(8, "little"))
        self._insert_free(start, csize)

    def _insert_free(self, start: int, size: int) -> None:
        chunks = self.free_chunks
        i = 0
        while i < len(chunks) and chunks[i][0] < start:
            i += 1
        chunks.insert(i, [start, size])
        # merge with next
        if i + 1 < len(chunks) and chunks[i][0] + chunks[i][1] == chunks[i + 1][0]:
            chunks[i][1] += chunks[i + 1][1]
            del chunks[i + 1]
        # merge with previous
        if i > 0 and chunks[i - 1][0] + chunks[i - 1][1] == chunks[i][0]:
            chunks[i - 1][1] += chunks[i][1]
            del chunks[i]

    def try_grow_in_place(self, user: int, csize: int, n: int) -> int | None:
        """Grow (or shrink) the chunk at ``user`` to fit ``n`` bytes. Returns the new chunk size."""
        start = user - HEADER
        need = chunk_size_for(n)
        if need <= csize:
            return csize
        end = start + csize
        extra = need - csize
        for i, (fs, fsize) in enumerate(self.free_chunks):
            if fs == end and fsize >= extra:
                if fsize - extra >= MIN_SPLIT:
                    self.free_chunks[i] = [fs + extra, fsize - extra]
                    new = need
                else:
                    del self.free_chunks[i]
                    new = csize + fsize
                self._rewrite_header(start, new)
                return new
            if fs == end and fs + fsize == self.brk and start + need <= self.limit:
                del self.free_chunks[i]
                self._grow(start + need)
                self._rewrite_header(start, need)
                return need
        if end == self.brk and start + need <= self.limit:
            self._grow(start + need)
            self._rewrite_header(start, need)
            return need
        return None

    def _rewrite_header(self, start: int, csize: int) -> None:
        self.mem.write(self.region, start, self.header_bytes(csize))

    def header_ok(self, user: int, csize: int) -> bool:
        start = user - HEADER
        r = self.region
        off = start - r.base
        return bytes(r.data[off : off + HEADER]) == self.header_bytes(csize)

    @property
    def used(self) -> int:
        return self.brk - self.base

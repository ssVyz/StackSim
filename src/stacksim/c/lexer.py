"""Turns C source text into tokens.

Preprocessor directives (lines starting with ``#``) are returned as single
``directive`` tokens holding the raw directive text; the preprocessor handles them.
"""

from __future__ import annotations

import re

from .errors import CompileError, Loc

KEYWORDS = frozenset(
    """auto break case char const continue default do double else enum extern float for goto if
    inline int long register restrict return short signed sizeof static struct switch typedef union
    unsigned void volatile while _Bool bool true false""".split()
)

PUNCTUATORS = sorted(
    """... <<= >>= -> ++ -- << >> <= >= == != && || += -= *= /= %= &= ^= |= ##
    [ ] ( ) { } . & * + - ~ ! / % < > ^ | ? : ; = , #""".split(),
    key=len,
    reverse=True,
)
_PUNCT_FIRST = frozenset(p[0] for p in PUNCTUATORS)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FLOAT = re.compile(r"(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+")
_HEX = re.compile(r"0[xX][0-9a-fA-F]+")
_BIN = re.compile(r"0[bB][01]+")
_DEC = re.compile(r"\d+")
_INT_SUFFIX = re.compile(r"(?:[uU](?:ll|LL|[lL])?|(?:ll|LL|[lL])[uU]?)")
_ALNUM = re.compile(r"[A-Za-z0-9_.]")

_SIMPLE_ESCAPES = {
    "n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, "'": 39, '"': 34,
    "a": 7, "b": 8, "f": 12, "v": 11, "?": 63, "e": 27,
}


class Token:
    __slots__ = ("kind", "value", "line", "col", "length", "header")

    def __init__(self, kind: str, value, line: int, col: int, length: int, header: str | None = None):
        self.kind = kind  # id kw int float char str punct directive eof
        self.value = value
        self.line = line
        self.col = col
        self.length = length
        self.header = header

    @property
    def loc(self) -> Loc:
        return Loc(self.line, self.col, self.length, self.header)

    def is_punct(self, p: str) -> bool:
        return self.kind == "punct" and self.value == p

    def is_kw(self, k: str) -> bool:
        return self.kind == "kw" and self.value == k

    def text(self) -> str:
        """An approximation of the token's spelling (used in messages and assert)."""
        if self.kind == "str":
            return '"' + self.value.decode("utf-8", "replace").encode("unicode_escape").decode() + '"'
        if self.kind == "char":
            return repr(chr(self.value & 0xFF))
        if self.kind in ("int", "float"):
            return self.value[2]
        if self.kind == "eof":
            return "end of file"
        return str(self.value)

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r}, {self.line}:{self.col})"


def _escape(src: str, i: int, line: int, col: int) -> tuple[int, int]:
    """Decode an escape sequence starting after the backslash at ``i``. Returns (byte, next index)."""
    if i >= len(src):
        raise CompileError("unfinished escape sequence", Loc(line, col))
    c = src[i]
    if c == "x":
        m = re.compile(r"[0-9a-fA-F]+").match(src, i + 1)
        if not m:
            raise CompileError("\\x must be followed by hexadecimal digits", Loc(line, col))
        return int(m.group(), 16) & 0xFF, m.end()
    if c in "01234567":
        m = re.compile(r"[0-7]{1,3}").match(src, i)
        return int(m.group(), 8) & 0xFF, m.end()
    if c in _SIMPLE_ESCAPES:
        return _SIMPLE_ESCAPES[c], i + 1
    raise CompileError(f"unknown escape sequence '\\{c}'", Loc(line, col), hint="Common escapes are \\n (newline), \\t (tab), \\\\ (backslash) and \\0 (null character).")


def tokenize(src: str, header: str | None = None) -> list[Token]:
    toks: list[Token] = []
    i = 0
    n = len(src)
    line = 1
    line_start = 0
    at_bol = True
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            line_start = i
            at_bol = True
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        if c == "\\" and i + 1 < n and src[i + 1] in "\r\n":
            i += 2 if src[i + 1] == "\n" else 3
            line += 1
            line_start = i
            continue
        col = i - line_start + 1
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise CompileError("comment started with /* is never closed with */", Loc(line, col, 2, header))
            for k in range(i, j):
                if src[k] == "\n":
                    line += 1
                    line_start = k + 1
            i = j + 2
            continue
        if c == "#" and at_bol:
            start_line = line
            j = i + 1
            parts = []
            seg_start = j
            while j < n and src[j] != "\n":
                if src[j] == "\\" and j + 1 < n and src[j + 1] == "\n":
                    parts.append(src[seg_start:j])
                    j += 2
                    line += 1
                    line_start = j
                    seg_start = j
                    continue
                j += 1
            parts.append(src[seg_start:j])
            toks.append(Token("directive", " ".join(parts), start_line, col, j - i, header))
            i = j
            continue
        at_bol = False

        m = _IDENT.match(src, i)
        if m:
            word = m.group()
            kind = "kw" if word in KEYWORDS else "id"
            toks.append(Token(kind, word, line, col, len(word), header))
            i = m.end()
            continue

        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            i = _number(src, i, line, col, toks, header)
            continue

        if c == "'":
            j = i + 1
            vals = []
            while j < n and src[j] != "'":
                if src[j] == "\n":
                    raise CompileError("character constant is missing its closing '", Loc(line, col, 1, header))
                if src[j] == "\\":
                    v, j = _escape(src, j + 1, line, col)
                    vals.append(v)
                else:
                    vals.extend(src[j].encode("utf-8"))
                    j += 1
            if j >= n:
                raise CompileError("character constant is missing its closing '", Loc(line, col, 1, header))
            if not vals:
                raise CompileError("empty character constant ''", Loc(line, col, 2, header))
            if len(vals) > 1:
                raise CompileError(
                    "a character constant must contain exactly one character",
                    Loc(line, col, j - i + 1, header),
                    hint="Use double quotes for strings: \"abc\". Only plain ASCII characters fit in a char.",
                )
            v = vals[0]
            if v > 127:
                v -= 256  # char is signed
            toks.append(Token("char", v, line, col, j - i + 1, header))
            i = j + 1
            continue

        if c == '"':
            j = i + 1
            buf = bytearray()
            while j < n and src[j] != '"':
                if src[j] == "\n":
                    raise CompileError('string literal is missing its closing "', Loc(line, col, 1, header))
                if src[j] == "\\":
                    v, j = _escape(src, j + 1, line, col)
                    buf.append(v)
                else:
                    buf.extend(src[j].encode("utf-8"))
                    j += 1
            if j >= n:
                raise CompileError('string literal is missing its closing "', Loc(line, col, 1, header))
            toks.append(Token("str", bytes(buf), line, col, j - i + 1, header))
            i = j + 1
            continue

        if c in _PUNCT_FIRST:
            for p in PUNCTUATORS:
                if src.startswith(p, i):
                    toks.append(Token("punct", p, line, col, len(p), header))
                    i += len(p)
                    break
            continue

        raise CompileError(f"unexpected character '{c}'", Loc(line, col, 1, header), hint="This character has no meaning in C here.")
    toks.append(Token("eof", None, line, i - line_start + 1, 0, header))
    return toks


def _number(src: str, i: int, line: int, col: int, toks: list[Token], header: str | None) -> int:
    for rx, base in ((_HEX, 16), (_BIN, 2)):
        m = rx.match(src, i)
        if m:
            value = int(m.group()[2:], base)
            j = m.end()
            sm = _INT_SUFFIX.match(src, j)
            suffix = sm.group().lower() if sm else ""
            j = sm.end() if sm else j
            _check_number_end(src, i, j, line, col, header)
            toks.append(Token("int", (value, suffix, src[i:j], False), line, col, j - i, header))
            return j
    m = _FLOAT.match(src, i)
    if m:
        j = m.end()
        suffix = ""
        if j < len(src) and src[j] in "fFlL":
            suffix = src[j].lower()
            j += 1
        _check_number_end(src, i, j, line, col, header)
        toks.append(Token("float", (float(m.group()), suffix, src[i:j]), line, col, j - i, header))
        return j
    m = _DEC.match(src, i)
    digits = m.group()
    j = m.end()
    if len(digits) > 1 and digits[0] == "0":
        if any(d in "89" for d in digits):
            raise CompileError(f"invalid octal number '{digits}'", Loc(line, col, j - i, header), hint="Numbers starting with 0 are octal (base 8) in C, so they may only use digits 0-7.")
        value = int(digits, 8)
        decimal = False
    else:
        value = int(digits)
        decimal = True
    sm = _INT_SUFFIX.match(src, j)
    suffix = sm.group().lower() if sm else ""
    j = sm.end() if sm else j
    _check_number_end(src, i, j, line, col, header)
    toks.append(Token("int", (value, suffix, src[i:j], decimal), line, col, j - i, header))
    return j


def _check_number_end(src: str, start: int, j: int, line: int, col: int, header: str | None) -> None:
    if j < len(src) and _ALNUM.match(src, j):
        end = j
        while end < len(src) and _ALNUM.match(src, end):
            end += 1
        raise CompileError(
            f"invalid number '{src[start:end]}'",
            Loc(line, col, end - start, header),
            hint="A number may not be directly followed by letters, and names may not start with a digit.",
        )

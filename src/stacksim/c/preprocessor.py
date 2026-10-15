"""A small C preprocessor: #include of the built-in headers, #define/#undef
(object-like and function-like macros, with # stringification),
#if/#ifdef/#ifndef/#elif/#else/#endif, #pragma and #error."""

from __future__ import annotations

import re
from collections import deque

from .errors import CompileError, Diagnostic, Loc
from .headers import HEADERS
from .lexer import Token, tokenize

_DIRECTIVE = re.compile(r"\s*([A-Za-z_]\w*)?(.*)", re.S)
_DEFINE = re.compile(r"\s*([A-Za-z_]\w*)(\()?(.*)", re.S)


class Macro:
    __slots__ = ("name", "params", "body")

    def __init__(self, name: str, params: list[str] | None, body: list[Token]):
        self.name = name
        self.params = params
        self.body = body


def _relocate(t: Token, at: Token) -> Token:
    return Token(t.kind, t.value, at.line, at.col, at.length, at.header)


class Preprocessor:
    def __init__(self) -> None:
        self.macros: dict[str, Macro] = {}
        self.included: list[str] = []
        self.diagnostics: list[Diagnostic] = []

    def run(self, tokens: list[Token]) -> list[Token]:
        out = self._process(tokens)
        eof = tokens[-1] if tokens and tokens[-1].kind == "eof" else Token("eof", None, 1, 1, 0)
        out.append(eof)
        return out

    # -- directive handling -------------------------------------------------

    def _process(self, tokens: list[Token]) -> list[Token]:
        out: list[Token] = []
        # Each entry: [parent_active, branch_active, any_branch_taken, seen_else, loc]
        conds: list[list] = []
        inp: deque[tuple[Token, frozenset]] = deque((t, frozenset()) for t in tokens if t.kind != "eof")

        def active() -> bool:
            return not conds or (conds[-1][0] and conds[-1][1])

        while inp:
            tok, hs = inp.popleft()
            if tok.kind == "directive":
                m = _DIRECTIVE.match(tok.value)
                name, rest = m.group(1) or "", m.group(2)
                if name in ("if", "ifdef", "ifndef"):
                    parent = active()
                    if not parent:
                        val = False
                    elif name == "if":
                        val = self._eval_if(rest, tok)
                    else:
                        mm = re.match(r"\s*([A-Za-z_]\w*)", rest)
                        if not mm:
                            raise CompileError(f"#{name} needs a macro name", tok.loc)
                        val = (mm.group(1) in self.macros) == (name == "ifdef")
                    conds.append([parent, val, val, False, tok.loc])
                elif name == "elif":
                    if not conds or conds[-1][3]:
                        raise CompileError("#elif without matching #if", tok.loc)
                    c = conds[-1]
                    if c[2] or not c[0]:
                        c[1] = False
                    else:
                        c[1] = self._eval_if(rest, tok)
                        c[2] = c[1]
                elif name == "else":
                    if not conds or conds[-1][3]:
                        raise CompileError("#else without matching #if", tok.loc)
                    c = conds[-1]
                    c[1] = not c[2]
                    c[3] = True
                elif name == "endif":
                    if not conds:
                        raise CompileError("#endif without matching #if", tok.loc)
                    conds.pop()
                elif not active():
                    pass
                elif name == "include":
                    out.extend(self._include(rest, tok))
                elif name == "define":
                    self._define(rest, tok)
                elif name == "undef":
                    self.macros.pop(rest.strip(), None)
                elif name in ("pragma", "line", ""):
                    pass
                elif name == "error":
                    raise CompileError(f"#error {rest.strip()}", tok.loc, kind="preprocessor")
                elif name == "warning":
                    self.diagnostics.append(Diagnostic("warning", f"#warning {rest.strip()}", tok.loc))
                else:
                    raise CompileError(f"unknown preprocessor directive '#{name}'", tok.loc, kind="preprocessor")
                continue
            if not active():
                continue
            if tok.kind == "id":
                if tok.value == "__LINE__":
                    out.append(Token("int", (tok.line, "", str(tok.line), True), tok.line, tok.col, tok.length, tok.header))
                    continue
                if tok.value == "__FILE__":
                    out.append(Token("str", b"main.c", tok.line, tok.col, tok.length, tok.header))
                    continue
                mac = self.macros.get(tok.value)
                if mac is not None and tok.value not in hs:
                    if mac.params is None:
                        repl = [_relocate(t, tok) for t in mac.body]
                    else:
                        if not inp or not inp[0][0].is_punct("("):
                            out.append(tok)
                            continue
                        args = self._collect_args(inp, tok, mac)
                        repl = self._substitute(mac, args, tok)
                    new_hs = hs | {tok.value}
                    for t in reversed(repl):
                        inp.appendleft((t, new_hs))
                    continue
            out.append(tok)
        if conds:
            raise CompileError("#if/#ifdef is never closed with #endif", conds[-1][4], kind="preprocessor")
        return out

    def _include(self, rest: str, tok: Token) -> list[Token]:
        rest = rest.strip()
        m = re.match(r'<\s*([^>]+?)\s*>|"([^"]+)"', rest)
        if not m:
            raise CompileError("#include expects <header.h>", tok.loc, kind="preprocessor")
        if m.group(2):
            raise CompileError(
                f'cannot include "{m.group(2)}": stacksim runs a single file, so only standard headers like <stdio.h> are available',
                tok.loc,
                kind="preprocessor",
            )
        name = m.group(1)
        if name not in HEADERS:
            avail = ", ".join(f"<{h}>" for h in sorted(HEADERS))
            raise CompileError(
                f"header <{name}> is not available in stacksim",
                tok.loc,
                hint=f"Available headers: {avail}",
                kind="preprocessor",
            )
        if name in self.included:
            return []
        self.included.append(name)
        htoks = tokenize(HEADERS[name], header=name)
        return self._process(htoks)

    def _define(self, rest: str, tok: Token) -> None:
        m = _DEFINE.match(rest)
        if not m:
            raise CompileError("#define needs a macro name", tok.loc, kind="preprocessor")
        name, paren, body = m.group(1), m.group(2), m.group(3)
        params = None
        if paren:
            close = body.find(")")
            if close < 0:
                raise CompileError(f"missing ')' in parameter list of macro {name}", tok.loc, kind="preprocessor")
            plist = body[:close].strip()
            params = [p.strip() for p in plist.split(",")] if plist else []
            for p in params:
                if not re.fullmatch(r"[A-Za-z_]\w*", p):
                    raise CompileError(f"invalid macro parameter '{p}'", tok.loc, kind="preprocessor")
            body = body[close + 1 :]
        try:
            btoks = tokenize(body, header=tok.header)[:-1]
        except CompileError as e:
            raise CompileError(e.diagnostic.message, tok.loc, kind="preprocessor") from None
        for t in btoks:
            t.line, t.col = tok.line, tok.col
        self.macros[name] = Macro(name, params, btoks)

    def _collect_args(self, inp: deque, name_tok: Token, mac: Macro) -> list[list[Token]]:
        inp.popleft()  # '('
        args: list[list[Token]] = [[]]
        depth = 0
        while True:
            if not inp:
                raise CompileError(f"call of macro {mac.name} is missing its closing ')'", name_tok.loc)
            t, _ = inp.popleft()
            if t.kind == "directive":
                continue
            if t.is_punct("(") or t.is_punct("[") or t.is_punct("{"):
                depth += 1
            elif t.is_punct(")") or t.is_punct("]") or t.is_punct("}"):
                if depth == 0 and t.is_punct(")"):
                    break
                depth -= 1
            elif t.is_punct(",") and depth == 0:
                args.append([])
                continue
            args[-1].append(t)
        if len(args) == 1 and not args[0] and not mac.params:
            args = []
        if len(args) != len(mac.params or []):
            raise CompileError(
                f"macro {mac.name} expects {len(mac.params or [])} argument(s) but got {len(args)}",
                name_tok.loc,
            )
        return args

    def _substitute(self, mac: Macro, args: list[list[Token]], at: Token) -> list[Token]:
        index = {p: i for i, p in enumerate(mac.params or [])}
        out: list[Token] = []
        body = mac.body
        i = 0
        while i < len(body):
            t = body[i]
            if t.is_punct("#") and i + 1 < len(body) and body[i + 1].kind == "id" and body[i + 1].value in index:
                arg = args[index[body[i + 1].value]]
                text = " ".join(a.text() for a in arg)
                out.append(Token("str", text.encode(), at.line, at.col, at.length, at.header))
                i += 2
                continue
            if t.is_punct("##"):
                raise CompileError("the ## operator is not supported in stacksim macros", at.loc, kind="preprocessor")
            if t.kind == "id" and t.value in index:
                out.extend(_relocate(a, at) for a in args[index[t.value]])
            else:
                out.append(_relocate(t, at))
            i += 1
        return out

    # -- #if expressions ----------------------------------------------------

    def _eval_if(self, text: str, tok: Token) -> bool:
        try:
            toks = tokenize(text)[:-1]
        except CompileError as e:
            raise CompileError(e.diagnostic.message, tok.loc, kind="preprocessor") from None
        # Resolve defined(X) / defined X before expanding macros.
        resolved: list[Token] = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.kind == "id" and t.value == "defined":
                if i + 1 < len(toks) and toks[i + 1].is_punct("("):
                    name = toks[i + 2].value if i + 2 < len(toks) else ""
                    i += 4
                else:
                    name = toks[i + 1].value if i + 1 < len(toks) else ""
                    i += 2
                v = 1 if name in self.macros else 0
                resolved.append(Token("int", (v, "", str(v), True), t.line, t.col, 1))
                continue
            resolved.append(t)
            i += 1
        saved = self.included
        expanded = self._process(resolved)
        self.included = saved
        ev = _IfEval(expanded, tok)
        val = ev.expr()
        if ev.pos != len(expanded):
            raise CompileError("could not understand this #if condition", tok.loc, kind="preprocessor")
        return bool(val)


class _IfEval:
    _BIN = [
        ("||",), ("&&",), ("|",), ("^",), ("&",), ("==", "!="), ("<", ">", "<=", ">="), ("<<", ">>"), ("+", "-"), ("*", "/", "%"),
    ]

    def __init__(self, toks: list[Token], at: Token):
        self.toks = toks
        self.pos = 0
        self.at = at

    def peek(self) -> Token | None:
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def fail(self):
        raise CompileError("could not understand this #if condition", self.at.loc, kind="preprocessor")

    def expr(self) -> int:
        c = self.binary(0)
        t = self.peek()
        if t is not None and t.is_punct("?"):
            self.pos += 1
            a = self.expr()
            t = self.peek()
            if t is None or not t.is_punct(":"):
                self.fail()
            self.pos += 1
            b = self.expr()
            return a if c else b
        return c

    def binary(self, level: int) -> int:
        if level == len(self._BIN):
            return self.unary()
        left = self.binary(level + 1)
        while True:
            t = self.peek()
            if t is None or t.kind != "punct" or t.value not in self._BIN[level]:
                return left
            self.pos += 1
            right = self.binary(level + 1)
            op = t.value
            if op == "||":
                left = int(bool(left) or bool(right))
            elif op == "&&":
                left = int(bool(left) and bool(right))
            elif op in ("/", "%"):
                if right == 0:
                    raise CompileError("division by zero in #if", self.at.loc, kind="preprocessor")
                left = int(left / right) if op == "/" else left - right * int(left / right)
            else:
                left = int(eval(f"{left} {op} {right}"))  # noqa: S307 - both operands are ints
        return left

    def unary(self) -> int:
        t = self.peek()
        if t is None:
            self.fail()
        self.pos += 1
        if t.kind == "punct":
            if t.value == "(":
                v = self.expr()
                t2 = self.peek()
                if t2 is None or not t2.is_punct(")"):
                    self.fail()
                self.pos += 1
                return v
            if t.value == "!":
                return int(not self.unary())
            if t.value == "-":
                return -self.unary()
            if t.value == "+":
                return self.unary()
            if t.value == "~":
                return ~self.unary()
        if t.kind == "int":
            return t.value[0]
        if t.kind == "char":
            return t.value
        if t.kind == "kw" and t.value == "true":
            return 1
        if t.kind in ("id", "kw"):
            return 0
        self.fail()

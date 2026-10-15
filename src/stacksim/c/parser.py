"""Recursive-descent parser for the supported C subset.

The parser builds C types directly (struct layouts, array sizes) and keeps
track of typedef names, which C needs to tell declarations from expressions.
Error messages are phrased for people who are new to C.
"""

from __future__ import annotations

from . import ast as A
from . import ctype as T
from .consteval import NotConstant, eval_const
from .errors import CompileError, Loc
from .headers import NAME_TO_HEADER, STD_TYPEDEF_NAMES
from .lexer import Token

TYPE_KW = frozenset("void char short int long float double signed unsigned _Bool bool struct union enum".split())
QUALIFIERS = frozenset("const volatile restrict inline register auto".split())
STORAGE = frozenset("typedef static extern".split())

BINPREC = {
    "||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, ">": 7, "<=": 7, ">=": 7,
    "<<": 8, ">>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10,
}
ASSIGN_OPS = frozenset("= += -= *= /= %= &= |= ^= <<= >>=".split())


def _describe(t: Token) -> str:
    if t.kind == "eof":
        return "the end of the file"
    if t.kind == "str":
        return "a string"
    if t.kind in ("int", "float"):
        return f"the number {t.value[2]}"
    if t.kind == "char":
        return "a character constant"
    if t.kind == "directive":
        return "a preprocessor line"
    return f"'{t.value}'"


class Declarator:
    __slots__ = ("name", "name_loc", "ptrs", "suffixes", "inner", "params")

    def __init__(self):
        self.name: str | None = None
        self.name_loc: Loc | None = None
        self.ptrs = 0
        self.suffixes: list = []
        self.inner: Declarator | None = None
        self.params: list[A.Param] | None = None

    def get_name(self) -> tuple[str | None, Loc | None]:
        if self.name is not None:
            return self.name, self.name_loc
        if self.inner is not None:
            return self.inner.get_name()
        return None, None


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.pos = 0
        # Ordinary identifier scopes: name -> ("typedef", CType) | ("var",) | ("enum", value)
        self.scopes: list[dict] = [{}]
        self.tags: list[dict] = [{}]
        self.pending_enums: list[A.EnumConst] = []
        self.current_function: str | None = None

    # ------------------------------------------------------------- helpers

    def peek(self, k: int = 0) -> Token:
        i = self.pos + k
        return self.toks[i] if i < len(self.toks) else self.toks[-1]

    def advance(self) -> Token:
        t = self.toks[self.pos]
        if t.kind != "eof":
            self.pos += 1
        return t

    def at(self, p: str) -> bool:
        t = self.toks[self.pos]
        return t.kind == "punct" and t.value == p

    def at_kw(self, k: str) -> bool:
        t = self.toks[self.pos]
        return t.kind == "kw" and t.value == k

    def accept(self, p: str) -> Token | None:
        t = self.toks[self.pos]
        if t.kind == "punct" and t.value == p:
            self.pos += 1
            return t
        return None

    def error(self, msg: str, tok: Token | Loc | None = None, hint: str | None = None) -> CompileError:
        if tok is None:
            tok = self.peek()
        loc = tok.loc if isinstance(tok, Token) else tok
        return CompileError(msg, loc, hint, kind="syntax")

    def expect(self, p: str, what: str | None = None) -> Token:
        t = self.peek()
        if t.kind == "punct" and t.value == p:
            return self.advance()
        if p == ";":
            self.expect_semi()
        msg = f"expected '{p}'" + (f" {what}" if what else "") + f" but found {_describe(t)}"
        raise self.error(msg, t)

    def expect_semi(self) -> None:
        if self.accept(";"):
            return
        prev = self.toks[self.pos - 1] if self.pos > 0 else self.peek()
        t = self.peek()
        if t.line != prev.line or t.kind == "eof":
            loc = Loc(prev.line, prev.col + prev.length, 1, prev.header)
            raise CompileError(
                "missing ';' at the end of this line",
                loc,
                hint="In C, every statement and declaration ends with a semicolon.",
            )
        raise self.error(f"expected ';' before {_describe(t)}", t, hint="Did you forget a semicolon or an operator?")

    def expect_ident(self, what: str) -> Token:
        t = self.peek()
        if t.kind != "id":
            if t.kind == "kw":
                raise self.error(f"'{t.value}' is a C keyword and cannot be used as {what}", t)
            raise self.error(f"expected {what} but found {_describe(t)}", t)
        return self.advance()

    # ------------------------------------------------------------ scopes

    def push_scope(self) -> None:
        self.scopes.append({})
        self.tags.append({})

    def pop_scope(self) -> None:
        self.scopes.pop()
        self.tags.pop()

    def lookup(self, name: str):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    def is_typedef(self, name: str) -> bool:
        e = self.lookup(name)
        return e is not None and e[0] == "typedef"

    def enum_value(self, name: str):
        e = self.lookup(name)
        return e[1] if e is not None and e[0] == "enum" else None

    def lookup_tag(self, tag: str):
        for s in reversed(self.tags):
            if tag in s:
                return s[tag]
        return None

    def starts_decl(self, t: Token) -> bool:
        if t.kind == "kw":
            return t.value in TYPE_KW or t.value in QUALIFIERS or t.value in STORAGE
        return t.kind == "id" and self.is_typedef(t.value)

    def starts_type_name(self, t: Token) -> bool:
        if t.kind == "kw":
            return t.value in TYPE_KW or t.value in ("const", "volatile")
        return t.kind == "id" and self.is_typedef(t.value)

    def const_int(self, e: A.Expr, what: str) -> int:
        try:
            return eval_const(e, self.enum_value)
        except NotConstant:
            raise CompileError(
                f"{what} must be a constant number known before the program runs",
                e.loc,
                hint="Variable-length arrays are not supported. To choose a size while the program runs, "
                "allocate the memory on the heap with malloc().",
                kind="semantic",
            ) from None

    # ------------------------------------------------------- translation unit

    def parse(self) -> A.TranslationUnit:
        items: list[A.Node] = []
        while self.peek().kind != "eof":
            t = self.peek()
            if t.is_punct(";"):
                self.advance()
                continue
            if t.is_punct("}"):
                raise self.error(
                    "unexpected '}': there are more closing braces than opening ones", t
                )
            if not self.starts_decl(t):
                self._unknown_type_check(t)
                if t.kind == "id" and self.peek(1).is_punct("("):
                    raise self.error(
                        f"expected a declaration, but '{t.value}(...)' looks like a function call or a definition without return type",
                        t,
                        hint="Statements must be inside a function such as main(). A function definition needs a return type, e.g. 'int main(void) { ... }'.",
                    )
                raise self.error(
                    f"expected a declaration but found {_describe(t)}",
                    t,
                    hint="At the top level of a C file there can only be declarations: functions, global variables, structs and typedefs.",
                )
            items.append(self.parse_declaration(top_level=True))
        return A.TranslationUnit(items)

    # ---------------------------------------------------------- declarations

    def parse_specifiers(self) -> tuple[T.CType, str | None, Token]:
        start = self.peek()
        storage = None
        base = None
        base_tok = None
        signed = None
        long_count = 0
        short = False
        int_seen = False
        any_spec = False
        while True:
            t = self.peek()
            if t.kind == "kw":
                v = t.value
                if v in QUALIFIERS:
                    self.advance()
                    continue
                if v in STORAGE:
                    if storage is not None:
                        raise self.error(f"'{v}' cannot be combined with '{storage}'", t)
                    storage = v
                    self.advance()
                    continue
                if v in ("signed", "unsigned"):
                    if signed is not None:
                        raise self.error(f"duplicate '{v}'", t)
                    signed = v == "signed"
                    any_spec = True
                    self.advance()
                    continue
                if v == "short":
                    short = True
                    any_spec = True
                    self.advance()
                    continue
                if v == "long":
                    long_count += 1
                    any_spec = True
                    self.advance()
                    continue
                if v == "int":
                    int_seen = True
                    any_spec = True
                    self.advance()
                    continue
                if v in ("char", "void", "float", "double", "_Bool", "bool"):
                    if base is not None:
                        raise self.error(f"two data types in one declaration ('{base_tok.value}' and '{v}')", t)
                    base = v
                    base_tok = t
                    any_spec = True
                    self.advance()
                    continue
                if v in ("struct", "union", "enum"):
                    if base is not None:
                        raise self.error("two data types in one declaration", t)
                    base_tok = t
                    base = self.parse_struct() if v != "enum" else self.parse_enum()
                    any_spec = True
                    continue
                break
            if t.kind == "id" and base is None and not any_spec and self.is_typedef(t.value):
                base = self.lookup(t.value)[1]
                base_tok = t
                any_spec = True
                self.advance()
                continue
            break
        if not any_spec:
            if storage is not None or start is not self.peek():
                raise self.error(f"expected a type but found {_describe(self.peek())}", self.peek())
            raise self.error(f"expected a type but found {_describe(start)}", start)

        if isinstance(base, T.CType):
            if signed is not None or short or long_count or int_seen:
                raise self.error("type modifiers like 'unsigned' or 'long' cannot be combined with this type", base_tok)
            return base, storage, start
        if base is None:
            if short and long_count:
                raise self.error("'short' and 'long' cannot be combined", start)
            if short:
                ty = T.SHORT if signed is not False else T.USHORT
            elif long_count == 1:
                ty = T.LONG if signed is not False else T.ULONG
            elif long_count >= 2:
                if long_count > 2:
                    raise self.error("'long long long' is too long", start)
                ty = T.LLONG if signed is not False else T.ULLONG
            else:
                ty = T.INT if signed is not False else T.UINT
            return ty, storage, start
        if base == "char":
            if short or long_count or int_seen:
                raise self.error("invalid combination of type modifiers with 'char'", base_tok)
            ty = T.CHAR if signed is None else (T.SCHAR if signed else T.UCHAR)
            return ty, storage, start
        if signed is not None or short or int_seen or (long_count and base != "double"):
            raise self.error(f"invalid combination of type modifiers with '{base}'", base_tok)
        if base == "double":
            return (T.LDOUBLE if long_count else T.DOUBLE), storage, start
        if base == "float":
            return T.FLOAT, storage, start
        if base in ("_Bool", "bool"):
            return T.BOOL, storage, start
        return T.VOID, storage, start

    def parse_struct(self) -> T.StructType:
        kw = self.advance()
        is_union = kw.value == "union"
        tag = None
        if self.peek().kind == "id":
            tag = self.advance().value
        if self.at("{"):
            st = None
            if tag is not None:
                existing = self.tags[-1].get(tag)
                if isinstance(existing, T.StructType) and not existing.complete:
                    st = existing
                elif existing is not None:
                    raise self.error(f"{kw.value} {tag} is defined twice", kw)
            if st is None:
                st = T.StructType(tag, is_union)
                if tag is not None:
                    self.tags[-1][tag] = st
            self.advance()
            members: list[tuple[str, T.CType]] = []
            names: set[str] = set()
            while not self.accept("}"):
                if self.peek().kind == "eof":
                    raise self.error(f"the '{{' of {kw.value} {tag or ''} is never closed", kw)
                base, storage, mt = self.parse_specifiers()
                if storage is not None:
                    raise self.error(f"'{storage}' is not allowed inside a {kw.value}", mt)
                while True:
                    d = self.parse_declarator()
                    name, nloc = d.get_name()
                    if name is None:
                        raise self.error("expected a member name", self.peek())
                    if self.at(":"):
                        raise self.error("bit-fields are not supported in stacksim", self.peek())
                    ty = self.build(d, base)
                    if isinstance(ty, T.FunctionType):
                        raise self.error(f"member '{name}' cannot be a function (use a function pointer)", nloc)
                    if not ty.complete:
                        if ty is st or (isinstance(ty, T.ArrayType) and ty.elem is st):
                            raise self.error(
                                f"a {kw.value} cannot contain itself",
                                nloc,
                                hint=f"Use a pointer instead: '{T.type_str(st)} *{name};'",
                            )
                        raise self.error(f"member '{name}' has incomplete type '{T.type_str(ty)}'", nloc)
                    if name in names:
                        raise self.error(f"duplicate member '{name}'", nloc)
                    names.add(name)
                    members.append((name, ty))
                    if not self.accept(","):
                        break
                self.expect_semi()
            if not members:
                raise self.error(f"a {kw.value} needs at least one member", kw)
            st.define(members)
            return st
        if tag is None:
            raise self.error(f"expected a name or '{{' after '{kw.value}'", self.peek())
        existing = self.lookup_tag(tag)
        if existing is None:
            st = T.StructType(tag, is_union)
            self.tags[-1][tag] = st
            return st
        if not isinstance(existing, T.StructType) or existing.is_union != is_union:
            raise self.error(f"'{tag}' was declared as a different kind of type", kw)
        return existing

    def parse_enum(self) -> T.CType:
        kw = self.advance()
        tag = None
        if self.peek().kind == "id":
            tag = self.advance().value
        if self.accept("{"):
            et = T.EnumType(tag)
            if tag is not None:
                self.tags[-1][tag] = et
            value = 0
            while not self.at("}"):
                nt = self.expect_ident("an enum constant name")
                if self.accept("="):
                    value = self.const_int(self.parse_conditional(), "an enum value")
                if nt.value in self.scopes[-1]:
                    raise self.error(f"'{nt.value}' is already declared", nt)
                self.scopes[-1][nt.value] = ("enum", value)
                self.pending_enums.append(A.EnumConst(nt.value, value, et, nt.loc))
                value += 1
                if not self.accept(","):
                    break
            self.expect("}", "to close the enum")
            return et
        if tag is None:
            raise self.error("expected a name or '{' after 'enum'", self.peek())
        existing = self.lookup_tag(tag)
        if existing is None:
            raise self.error(f"unknown enum '{tag}'", kw)
        return existing

    def is_nested_declarator(self) -> bool:
        nxt = self.peek(1)
        if nxt.kind == "punct" and nxt.value in ("*", "(", "["):
            return True
        return nxt.kind == "id" and not self.is_typedef(nxt.value)

    def parse_declarator(self, abstract: bool = False) -> Declarator:
        d = Declarator()
        while self.accept("*"):
            d.ptrs += 1
            while self.peek().kind == "kw" and self.peek().value in ("const", "volatile", "restrict"):
                self.advance()
        t = self.peek()
        if t.kind == "id" and not (abstract and self.is_typedef(t.value)):
            d.name = t.value
            d.name_loc = t.loc
            self.advance()
        elif t.is_punct("(") and self.is_nested_declarator():
            self.advance()
            d.inner = self.parse_declarator(abstract)
            self.expect(")", "to close the declarator")
        while True:
            if self.at("["):
                lb = self.advance()
                if self.accept("]"):
                    d.suffixes.append(("arr", None, lb))
                    continue
                e = self.parse_assign()
                n = self.const_int(e, "the size of an array")
                if n <= 0:
                    raise self.error("the size of an array must be greater than zero", e.loc)
                self.expect("]", "to close the array size")
                d.suffixes.append(("arr", n, lb))
            elif self.at("("):
                params, variadic, prototyped = self.parse_params()
                d.suffixes.append(("fn", params, variadic, prototyped))
            else:
                break
        if d.inner is None and d.suffixes and d.suffixes[0][0] == "fn":
            d.params = d.suffixes[0][1]
        return d

    def build(self, d: Declarator, base: T.CType) -> T.CType:
        t = base
        for _ in range(d.ptrs):
            t = T.PointerType(t)
        for s in reversed(d.suffixes):
            if s[0] == "arr":
                if isinstance(t, T.FunctionType):
                    raise self.error("an array of functions is not possible (use function pointers)", s[2])
                if not t.complete and not isinstance(t, T.VoidType):
                    raise self.error(f"array elements have incomplete type '{T.type_str(t)}'", s[2])
                if isinstance(t, T.VoidType):
                    raise self.error("an array of void is not possible", s[2])
                t = T.ArrayType(t, s[1])
            else:
                if isinstance(t, (T.ArrayType, T.FunctionType)):
                    raise self.error("a function cannot return an array or a function (return a pointer instead)", d.name_loc or self.peek())
                t = T.FunctionType(t, [p.ty for p in s[1]], s[2], s[3])
        if d.inner is not None:
            t = self.build(d.inner, t)
        return t

    def parse_params(self) -> tuple[list[A.Param], bool, bool]:
        self.expect("(")
        if self.accept(")"):
            return [], False, False
        if self.at_kw("void") and self.peek(1).is_punct(")"):
            self.advance()
            self.advance()
            return [], False, True
        params: list[A.Param] = []
        variadic = False
        while True:
            if self.accept("..."):
                variadic = True
                break
            t = self.peek()
            if not self.starts_decl(t):
                self._unknown_type_check(t, in_params=True)
                raise self.error(
                    f"expected a parameter type but found {_describe(t)}",
                    t,
                    hint="Each parameter needs a type and a name, e.g. 'int count'.",
                )
            base, storage, _ = self.parse_specifiers()
            d = self.parse_declarator(abstract=True)
            ty = self.build(d, base)
            if isinstance(ty, T.ArrayType):
                ty = T.PointerType(ty.elem)
            elif isinstance(ty, T.FunctionType):
                ty = T.PointerType(ty)
            name, nloc = d.get_name()
            if isinstance(ty, T.VoidType):
                raise self.error("a parameter cannot have type void", t, hint="Write '(void)' alone for a function without parameters.")
            params.append(A.Param(name, ty, nloc or t.loc))
            if not self.accept(","):
                break
        self.expect(")", "to close the parameter list")
        return params, variadic, True

    def parse_type_name(self) -> T.CType:
        base, storage, t = self.parse_specifiers()
        if storage is not None:
            raise self.error(f"'{storage}' is not allowed here", t)
        d = self.parse_declarator(abstract=True)
        if d.get_name()[0] is not None:
            raise self.error("unexpected name in type", d.get_name()[1])
        return self.build(d, base)

    def declare_name(self, name: str, kind: tuple, loc: Loc) -> None:
        self.scopes[-1][name] = kind

    def parse_declaration(self, top_level: bool) -> A.Node:
        self.pending_enums = []
        base, storage, start = self.parse_specifiers()
        decls: list[A.Node] = list(self.pending_enums)
        self.pending_enums = []
        if self.accept(";"):
            return A.DeclStmt(decls, start.loc)
        first = True
        while True:
            d = self.parse_declarator()
            name, nloc = d.get_name()
            if name is None:
                raise self.error(f"expected a name in this declaration but found {_describe(self.peek())}", self.peek())
            ty = self.build(d, base)
            if storage == "typedef":
                prev = self.scopes[-1].get(name)
                if prev is not None and (prev[0] != "typedef" or prev[1] != ty):
                    raise self.error(f"'{name}' is already declared differently", nloc)
                if isinstance(ty, T.StructType) and ty.tag is None and ty.display_name is None:
                    ty.display_name = name
                elif isinstance(ty, T.IntType) and not isinstance(ty, T.EnumType) and d.ptrs == 0 and not d.suffixes:
                    ty = ty.alias(name)
                self.declare_name(name, ("typedef", ty), nloc)
            elif isinstance(ty, T.FunctionType):
                self.declare_name(name, ("var",), nloc)
                if self.at("{"):
                    if not top_level:
                        raise self.error(
                            f"function '{name}' cannot be defined inside another function",
                            nloc,
                            hint=f"Move the definition of {name}() outside of {self.current_function}(), e.g. above it.",
                        )
                    if not first:
                        raise self.error("a function definition must be its own declaration", nloc)
                    return self.parse_function_body(name, nloc, ty, d, storage)
                decls.append(A.FuncDecl(name, ty, storage, nloc))
            else:
                if isinstance(ty, T.VoidType):
                    raise self.error(f"variable '{name}' cannot have type void", nloc, hint="void means 'no value'. Did you mean a pointer, 'void *'?")
                self.declare_name(name, ("var",), nloc)
                init = None
                if self.accept("="):
                    init = self.parse_initializer()
                decls.append(A.VarDecl(name, ty, init, storage, start.loc, nloc))
            first = False
            if self.accept(","):
                continue
            if self.at("{") and isinstance(ty, T.FunctionType) is False and storage != "typedef":
                raise self.error("unexpected '{' after a variable declaration", self.peek())
            self.expect_semi()
            break
        return A.DeclStmt(decls, start.loc)

    def parse_function_body(self, name: str, nloc: Loc, ty: T.FunctionType, d: Declarator, storage) -> A.FuncDef:
        params = d.params if d.params is not None else []
        for i, p in enumerate(params):
            if p.name is None:
                raise self.error(f"parameter {i + 1} of '{name}' needs a name", p.loc)
        self.push_scope()
        for p in params:
            self.declare_name(p.name, ("var",), p.loc)
        prev = self.current_function
        self.current_function = name
        body = self.parse_compound(new_scope=False)
        self.current_function = prev
        self.pop_scope()
        return A.FuncDef(name, ty, params, body, storage, nloc)

    def parse_initializer(self) -> A.Expr:
        if self.at("{"):
            lb = self.advance()
            items: list[A.InitItem] = []
            while not self.at("}"):
                t = self.peek()
                desig = []
                while self.at(".") or self.at("["):
                    if self.accept("."):
                        desig.append(("field", self.expect_ident("a member name").value))
                    else:
                        self.advance()
                        desig.append(("index", self.parse_conditional()))
                        self.expect("]")
                if desig:
                    self.expect("=", "after the designator")
                items.append(A.InitItem(desig, self.parse_initializer(), t.loc))
                if not self.accept(","):
                    break
            self.expect("}", "to close the initializer list")
            return A.InitList(items, lb.loc)
        return self.parse_assign()

    def _unknown_type_check(self, t: Token, in_params: bool = False) -> None:
        """Turn 'size_t n' or 'Foo x' into a helpful 'unknown type' message."""
        if t.kind != "id":
            return
        nxt = self.peek(1)
        looks_like_decl = nxt.kind == "id" or (
            nxt.is_punct("*") and self.peek(2).kind == "id" and self.peek(3).kind == "punct" and self.peek(3).value in ("=", ";", ",", "[", ")")
        )
        if in_params:
            looks_like_decl = looks_like_decl or nxt.is_punct(",") or nxt.is_punct(")")
        if not looks_like_decl:
            return
        name = t.value
        if name in STD_TYPEDEF_NAMES:
            raise self.error(
                f"unknown type name '{name}'",
                t,
                hint=f"'{name}' is defined in <{NAME_TO_HEADER[name]}>. Add '#include <{NAME_TO_HEADER[name]}>' at the top of the file.",
            )
        tag = self.lookup_tag(name)
        if tag is not None:
            kw = "enum" if isinstance(tag, T.EnumType) else tag.keyword
            raise self.error(
                f"unknown type name '{name}'",
                t,
                hint=f"There is a '{kw} {name}'. In C you have to write '{kw} {name}', or create a typedef.",
            )
        if name == "string":
            raise self.error("unknown type name 'string'", t, hint="C has no string type. Strings are char arrays (char s[20]) or pointers to char (char *s).")
        if name in ("boolean", "Boolean"):
            raise self.error(f"unknown type name '{name}'", t, hint="Use 'bool' (or 'int').")
        raise self.error(f"unknown type name '{name}'", t)

    # ------------------------------------------------------------ statements

    def parse_compound(self, new_scope: bool = True) -> A.Compound:
        lb = self.expect("{")
        if new_scope:
            self.push_scope()
        items: list[A.Stmt] = []
        while not self.at("}"):
            if self.peek().kind == "eof":
                raise self.error(
                    "this '{' is never closed by a matching '}'",
                    lb,
                    hint="Check that every '{' has a matching '}'.",
                )
            items.append(self.parse_statement())
        rb = self.advance()
        if new_scope:
            self.pop_scope()
        return A.Compound(items, lb.loc, rb.loc)

    def parse_statement(self) -> A.Stmt:
        t = self.peek()
        if t.kind == "punct":
            if t.value == "{":
                return self.parse_compound()
            if t.value == ";":
                self.advance()
                return A.Empty(t.loc)
        if t.kind == "kw":
            v = t.value
            if v == "if":
                self.advance()
                self.expect("(", "after 'if'")
                cond = self.parse_expr()
                self.expect(")", "to close the if condition")
                then = self.parse_substatement()
                other = None
                if self.at_kw("else"):
                    self.advance()
                    other = self.parse_substatement()
                return A.If(cond, then, other, t.loc)
            if v == "while":
                self.advance()
                self.expect("(", "after 'while'")
                cond = self.parse_expr()
                self.expect(")", "to close the while condition")
                return A.While(cond, self.parse_substatement(), t.loc)
            if v == "do":
                self.advance()
                body = self.parse_substatement()
                wt = self.peek()
                if not self.at_kw("while"):
                    raise self.error("expected 'while' after the body of a do loop", wt)
                self.advance()
                self.expect("(", "after 'while'")
                cond = self.parse_expr()
                self.expect(")", "to close the while condition")
                self.expect_semi()
                return A.DoWhile(body, cond, t.loc, wt.loc)
            if v == "for":
                self.advance()
                self.expect("(", "after 'for'")
                self.push_scope()
                if self.accept(";"):
                    init = None
                elif self.starts_decl(self.peek()):
                    init = self.parse_declaration(top_level=False)
                else:
                    e = self.parse_expr()
                    init = A.ExprStmt(e, e.loc)
                    self.expect(";", "after the first part of the for loop")
                cond = None if self.at(";") else self.parse_expr()
                self.expect(";", "after the condition of the for loop")
                step = None if self.at(")") else self.parse_expr()
                self.expect(")", "to close the for loop header")
                body = self.parse_substatement()
                self.pop_scope()
                return A.For(init, cond, step, body, t.loc)
            if v == "switch":
                self.advance()
                self.expect("(", "after 'switch'")
                e = self.parse_expr()
                self.expect(")", "to close the switch value")
                return A.Switch(e, self.parse_substatement(), t.loc)
            if v == "case":
                self.advance()
                e = self.parse_conditional()
                self.expect(":", "after the case value")
                return A.Case(e, self.parse_case_body(), t.loc)
            if v == "default":
                self.advance()
                self.expect(":", "after 'default'")
                return A.Case(None, self.parse_case_body(), t.loc)
            if v == "break":
                self.advance()
                self.expect_semi()
                return A.Break(t.loc)
            if v == "continue":
                self.advance()
                self.expect_semi()
                return A.Continue(t.loc)
            if v == "return":
                self.advance()
                e = None if self.at(";") else self.parse_expr()
                self.expect_semi()
                return A.Return(e, t.loc)
            if v == "goto":
                raise self.error("goto is not supported in stacksim", t, hint="Use loops, break and continue instead.")
            if v == "else":
                raise self.error("'else' without a matching 'if'", t, hint="Check for a stray ';' right after the if condition, or missing braces.")
        if self.starts_decl(t):
            return self.parse_declaration(top_level=False)
        if t.kind == "id" and self.peek(1).is_punct(":") and not self.peek(2).is_punct(":"):
            raise self.error("labels and goto are not supported in stacksim", t)
        self._unknown_type_check(t)
        e = self.parse_expr()
        self.expect_semi()
        return A.ExprStmt(e, t.loc)

    def parse_substatement(self) -> A.Stmt:
        t = self.peek()
        if self.starts_decl(t) and not (t.kind == "id"):
            raise self.error(
                "a declaration is not allowed directly here",
                t,
                hint="Put the body in braces: { ... }",
            )
        return self.parse_statement()

    def parse_case_body(self) -> A.Stmt:
        if self.at("}"):
            return A.Empty(self.peek().loc)
        return self.parse_statement()

    # ----------------------------------------------------------- expressions

    def parse_expr(self) -> A.Expr:
        e = self.parse_assign()
        while self.at(","):
            t = self.advance()
            e = A.Comma(e, self.parse_assign(), t.loc)
        return e

    def parse_assign(self) -> A.Expr:
        left = self.parse_conditional()
        t = self.peek()
        if t.kind == "punct" and t.value in ASSIGN_OPS:
            self.advance()
            right = self.parse_assign()
            return A.Assign(t.value, left, right, t.loc)
        return left

    def parse_conditional(self) -> A.Expr:
        c = self.parse_binary(1)
        if self.at("?"):
            q = self.advance()
            a = self.parse_expr()
            self.expect(":", "in the ?: expression")
            b = self.parse_conditional()
            return A.Cond(c, a, b, q.loc)
        return c

    def parse_binary(self, minprec: int) -> A.Expr:
        left = self.parse_cast()
        while True:
            t = self.peek()
            if t.kind != "punct":
                return left
            p = BINPREC.get(t.value)
            if p is None or p < minprec:
                return left
            self.advance()
            right = self.parse_binary(p + 1)
            left = A.Binary(t.value, left, right, t.loc)

    def parse_cast(self) -> A.Expr:
        if self.at("(") and self.starts_type_name(self.peek(1)):
            lp = self.advance()
            ty = self.parse_type_name()
            self.expect(")", "to close the cast")
            if self.at("{"):
                raise self.error("compound literals like (type){...} are not supported in stacksim", self.peek())
            return A.Cast(ty, self.parse_cast(), lp.loc)
        return self.parse_unary()

    def parse_unary(self) -> A.Expr:
        t = self.peek()
        if t.kind == "punct":
            if t.value in ("++", "--"):
                self.advance()
                return A.Unary(t.value, self.parse_unary(), t.loc)
            if t.value in ("-", "+", "!", "~", "*", "&"):
                self.advance()
                return A.Unary(t.value, self.parse_cast(), t.loc)
        if t.is_kw("sizeof"):
            self.advance()
            if self.at("(") and self.starts_type_name(self.peek(1)):
                self.advance()
                ty = self.parse_type_name()
                self.expect(")", "to close sizeof")
                return A.SizeofType(ty, t.loc)
            return A.SizeofExpr(self.parse_unary(), t.loc)
        return self.parse_postfix()

    def parse_postfix(self) -> A.Expr:
        e = self.parse_primary()
        while True:
            t = self.peek()
            if t.kind != "punct":
                return e
            if t.value == "[":
                self.advance()
                idx = self.parse_expr()
                self.expect("]", "to close the index")
                e = A.Index(e, idx, t.loc)
            elif t.value == "(":
                self.advance()
                args: list[A.Expr] = []
                if not self.at(")"):
                    while True:
                        args.append(self.parse_assign())
                        if not self.accept(","):
                            break
                self.expect(")", "to close the argument list")
                e = A.Call(e, args, t.loc)
            elif t.value in (".", "->"):
                self.advance()
                name = self.expect_ident("a member name")
                e = A.Member(e, name.value, t.value == "->", t.loc)
            elif t.value in ("++", "--"):
                self.advance()
                e = A.Postfix(t.value, e, t.loc)
            else:
                return e

    def parse_primary(self) -> A.Expr:
        t = self.peek()
        k = t.kind
        if k == "int":
            self.advance()
            value, suffix, text, decimal = t.value
            return A.IntLit(value, self.int_lit_type(value, suffix, decimal, t), t.loc, text)
        if k == "float":
            self.advance()
            value, suffix, text = t.value
            ty = T.FLOAT if suffix == "f" else (T.LDOUBLE if suffix == "l" else T.DOUBLE)
            if ty is T.FLOAT:
                value = T.float32(value)
            return A.FloatLit(value, ty, t.loc, text)
        if k == "char":
            self.advance()
            return A.IntLit(t.value, T.INT, t.loc, t.text())
        if k == "str":
            data = bytearray()
            while self.peek().kind == "str":
                data.extend(self.advance().value)
            end = self.toks[self.pos - 1]
            length = end.col + end.length - t.col if end.line == t.line else t.length
            return A.StrLit(bytes(data), Loc(t.line, t.col, length, t.header))
        if k == "id":
            self.advance()
            return A.Ident(t.value, t.loc)
        if k == "kw":
            if t.value in ("true", "false"):
                self.advance()
                return A.IntLit(1 if t.value == "true" else 0, T.BOOL, t.loc, t.value)
            if self.starts_type_name(t):
                raise self.error(f"unexpected type '{t.value}' in an expression", t, hint="To convert a value, write a cast like '(int)x'.")
            raise self.error(f"unexpected keyword '{t.value}' in an expression", t)
        if t.is_punct("("):
            self.advance()
            e = self.parse_expr()
            self.expect(")", "to close the parenthesis")
            return e
        if t.kind == "eof":
            raise self.error("the file ends in the middle of an expression", t)
        raise self.error(f"expected an expression but found {_describe(t)}", t)

    def int_lit_type(self, value: int, suffix: str, decimal: bool, t: Token) -> T.CType:
        unsigned = "u" in suffix
        longs = suffix.count("l")
        if unsigned:
            cands = [T.UINT, T.ULONG, T.ULLONG]
        elif decimal:
            cands = [T.INT, T.LONG, T.LLONG]
        else:
            cands = [T.INT, T.UINT, T.LONG, T.ULONG, T.LLONG, T.ULLONG]
        if longs == 1:
            cands = [c for c in cands if c.rank >= 4]
        elif longs == 2:
            cands = [c for c in cands if c.rank >= 5]
        for c in cands:
            if value <= c.max:
                return c
        if value <= T.ULLONG.max:
            return T.ULLONG
        raise self.error("this number is too large for any integer type", t)


def parse(tokens: list[Token]) -> A.TranslationUnit:
    return Parser(tokens).parse()

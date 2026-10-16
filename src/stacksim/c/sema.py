"""Semantic checking: name resolution, type checking, implicit conversions,
initializer layout, and beginner-oriented warnings (e.g. printf/scanf formats)."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from . import ast as A
from . import ctype as T
from .consteval import NotConstant, eval_const
from .errors import CompileError, Diagnostic, Loc
from .headers import NAME_TO_HEADER


class Symbol:
    __slots__ = (
        "name", "kind", "ty", "storage", "loc", "id", "node", "builtin", "value", "uses",
        "addr", "obj", "func_index", "owner",
    )

    def __init__(self, name: str, kind: str, ty: T.CType, storage: str, loc: Loc | None):
        self.name = name
        self.kind = kind  # var | func | enum
        self.ty = ty
        self.storage = storage  # global | local | param | static | builtin
        self.loc = loc
        self.id = -1
        self.node = None  # FuncDef for defined functions, VarDecl for variables
        self.builtin = False
        self.value = None  # enum value
        self.uses: list[Loc] = []
        self.addr = 0  # used by the interpreter for globals/statics
        self.obj = -1
        self.func_index = -1
        self.owner: str | None = None  # function name for static locals

    def __repr__(self) -> str:
        return f"<Symbol {self.kind} {self.name}: {T.type_str(self.ty)}>"


@dataclass
class Program:
    functions: dict[str, A.FuncDef]
    func_symbols: list[Symbol]  # all callable symbols with definitions, in code-address order
    globals: list[A.VarDecl]  # globals and static locals, in definition order
    builtin_globals: list[Symbol]
    literals: list[bytes]
    main: A.FuncDef
    headers: list[str]
    diagnostics: list[Diagnostic] = field(default_factory=list)
    reads_stdin: bool = False  # the program calls scanf, getchar, fgets, ...

    @property
    def takes_args(self) -> bool:
        """main() is declared with (int argc, char *argv[])."""
        return len(self.main.params) == 2


PRINTF_FMT_INDEX = {"printf": 0, "fprintf": 1, "sprintf": 1, "snprintf": 2}
SCANF_FMT_INDEX = {"scanf": 0, "sscanf": 1}
INPUT_FUNCTIONS = frozenset({"scanf", "getchar", "fgets", "fgetc", "getc"})
_FMT_RE = re.compile(r"%([-+ #0]*)(\*|\d+)?(?:\.(\*|\d*))?(hh|h|ll|l|z|j|t|L)?([a-zA-Z%\[])?")


def _is_null_constant(e: A.Expr) -> bool:
    while isinstance(e, A.Cast) and (T.is_integer(e.ty) or (T.is_pointer(e.ty) and T.is_void(e.ty.target))):
        e = e.expr
    return isinstance(e, A.IntLit) and e.value == 0


def _is_lvalue(e: A.Expr) -> bool:
    if isinstance(e, A.Ident):
        return e.sym is not None and e.sym.kind == "var"
    if isinstance(e, A.Unary):
        return e.op == "*"
    if isinstance(e, (A.Index, A.StrLit)):
        return True
    if isinstance(e, A.Member):
        return e.arrow or _is_lvalue(e.obj)
    return False


def _strip(e: A.Expr) -> A.Expr:
    while isinstance(e, (A.Cast, A.Decay)) and (not isinstance(e, A.Cast) or e.implicit):
        e = e.expr
    return e


class Sema:
    def __init__(self, headers: list[str]):
        self.headers = headers
        self.scopes: list[dict[str, Symbol]] = [{}]
        self.diags: list[Diagnostic] = []
        self.next_id = 0
        self.functions: dict[str, A.FuncDef] = {}
        self.func_syms: list[Symbol] = []
        self.globals: list[A.VarDecl] = []
        self.builtin_globals: list[Symbol] = []
        self.literals: list[bytes] = []
        self.literal_index: dict[bytes, int] = {}
        self.func: A.FuncDef | None = None
        self.loop_depth = 0
        self.switch_depth = 0
        self.reads_stdin = False

    # ----------------------------------------------------------- utilities

    def warn(self, msg: str, loc: Loc | None, hint: str | None = None, kind: str = "") -> None:
        if loc is not None and loc.header:
            return
        self.diags.append(Diagnostic("warning", msg, loc, hint, kind=kind))

    def err(self, msg: str, loc: Loc | None, hint: str | None = None) -> CompileError:
        return CompileError(msg, loc, hint, kind="semantic")

    def new_symbol(self, name, kind, ty, storage, loc) -> Symbol:
        s = Symbol(name, kind, ty, storage, loc)
        s.id = self.next_id
        self.next_id += 1
        return s

    def lookup(self, name: str) -> Symbol | None:
        for s in reversed(self.scopes):
            sym = s.get(name)
            if sym is not None:
                return sym
        return None

    def visible_names(self) -> list[str]:
        names = set()
        for s in self.scopes:
            names.update(n for n, sym in s.items() if not (sym.loc and sym.loc.header and n.startswith("__")))
        return sorted(names)

    def declare(self, sym: Symbol) -> None:
        scope = self.scopes[-1]
        prev = scope.get(sym.name)
        if prev is not None:
            raise self.err(
                f"'{sym.name}' is already declared in this scope (at line {prev.loc.line})" if prev.loc and prev.loc.line else f"'{sym.name}' is already declared",
                sym.loc,
                hint="Each variable name can only be declared once per block. Pick a different name, or remove the type to assign to the existing variable.",
            )
        scope[sym.name] = sym

    def undeclared(self, name: str, loc: Loc, is_call: bool) -> CompileError:
        hdr = NAME_TO_HEADER.get(name)
        what = "function" if is_call else "identifier"
        if hdr is not None and hdr not in self.headers:
            return self.err(f"'{name}' is not declared", loc, hint=f"'{name}' is part of the C standard library. Add '#include <{hdr}>' at the top of the file.")
        close = difflib.get_close_matches(name, self.visible_names(), n=1, cutoff=0.75)
        hint = f"Did you mean '{close[0]}'?" if close else ("Variables must be declared (with a type) before they are used." if not is_call else "Functions must be declared before they are called; define it above, or add a prototype.")
        return self.err(f"{what} '{name}' is not declared", loc, hint=hint)

    def literal(self, data: bytes) -> int:
        idx = self.literal_index.get(data)
        if idx is None:
            idx = len(self.literals)
            self.literals.append(data)
            self.literal_index[data] = idx
        return idx

    def const_value(self, e: A.Expr, what: str) -> int:
        def enum_value(name):
            s = self.lookup(name)
            return s.value if s is not None and s.kind == "enum" else None

        try:
            return eval_const(e, enum_value)
        except NotConstant:
            raise self.err(f"{what} must be a constant", e.loc) from None

    # ------------------------------------------------------------ program

    def check(self, tu: A.TranslationUnit) -> Program:
        for item in tu.items:
            if isinstance(item, A.FuncDef):
                self.function_def(item)
            elif isinstance(item, A.DeclStmt):
                self.decl_stmt(item, top_level=True)
        main = self.functions.get("main")
        if main is None:
            raise self.err(
                "the program has no main() function",
                Loc(1, 1),
                hint="Every C program starts running in main. Add:  int main(void) { ... return 0; }",
            )
        self.check_main(main)
        for sym in self.scopes[0].values():
            if sym.kind == "func" and not sym.builtin and sym.node is None and sym.uses:
                raise self.err(
                    f"function '{sym.name}' is declared but never defined",
                    sym.uses[0],
                    hint=f"Write the body of {sym.name}() somewhere in the file.",
                )
        return Program(
            functions=self.functions,
            func_symbols=self.func_syms,
            globals=self.globals,
            builtin_globals=self.builtin_globals,
            literals=self.literals,
            main=main,
            headers=self.headers,
            diagnostics=self.diags,
            reads_stdin=self.reads_stdin,
        )

    def check_main(self, main: A.FuncDef) -> None:
        ft = main.ty
        if not (isinstance(ft.ret, T.IntType) and ft.ret.rank == 3 and ft.ret.signed):
            self.warn("main() should return int", main.loc, hint="Write 'int main(void)' and end it with 'return 0;'.")
        n = len(ft.params)
        if n not in (0, 2):
            raise self.err("main() must take no parameters or (int argc, char *argv[])", main.loc)
        if n == 2:
            a, b = ft.params
            ok = T.is_integer(a) and isinstance(b, T.PointerType) and isinstance(b.target, T.PointerType) and T.is_integer(b.target.target)
            if not ok:
                raise self.err("the parameters of main() must be (int argc, char *argv[])", main.loc)

    def function_def(self, fd: A.FuncDef) -> None:
        sym = self.declare_function(fd.name, fd.ty, fd.loc, header=None)
        if sym.node is not None:
            raise self.err(f"function '{fd.name}' is defined twice (first at line {sym.node.loc.line})", fd.loc)
        if sym.builtin:
            raise self.err(f"'{fd.name}' is a standard library function and cannot be redefined", fd.loc, hint="Choose a different name for your function.")
        sym.node = fd
        sym.ty = fd.ty
        fd.sym = sym
        sym.func_index = len(self.func_syms)
        self.func_syms.append(sym)
        self.functions[fd.name] = fd
        ret = fd.ty.ret
        if isinstance(ret, T.StructType) and not ret.complete:
            raise self.err(f"return type '{T.type_str(ret)}' is incomplete", fd.loc)

        self.func = fd
        self.scopes.append({})
        for p in fd.params:
            if isinstance(p.ty, T.StructType) and not p.ty.complete:
                raise self.err(f"parameter '{p.name}' has incomplete type", p.loc)
            ps = self.new_symbol(p.name, "var", p.ty, "param", p.loc)
            self.declare(ps)
            p.sym = ps
        for item in fd.body.items:
            self.stmt(item)
        self.scopes.pop()
        self.func = None
        if not T.is_void(ret) and fd.name != "main" and not self._always_returns(fd.body):
            self.warn(
                f"the end of '{fd.name}' can be reached without a return statement",
                fd.body.end_loc,
                hint=f"{fd.name}() promises to return {T.a_type(ret)}; every path through it should end with 'return ...;'.",
            )

    def _always_returns(self, s: A.Stmt) -> bool:
        if isinstance(s, A.Return):
            return True
        if isinstance(s, A.Compound):
            return any(self._always_returns(i) for i in s.items)
        if isinstance(s, A.If):
            return s.other is not None and self._always_returns(s.then) and self._always_returns(s.other)
        if isinstance(s, (A.While, A.For)):
            cond = s.cond
            return cond is None or (isinstance(cond, A.IntLit) and cond.value != 0) or (
                isinstance(cond, A.Cast) and isinstance(cond.expr, A.IntLit) and cond.expr.value != 0
            )
        if isinstance(s, A.DoWhile):
            return self._always_returns(s.body)
        if isinstance(s, A.ExprStmt):
            e = s.expr
            return isinstance(e, A.Call) and e.fsym is not None and e.fsym.name in ("exit", "abort")
        return False

    def declare_function(self, name: str, ty: T.FunctionType, loc: Loc, header: str | None) -> Symbol:
        g = self.scopes[0]
        sym = g.get(name)
        if sym is not None:
            if sym.kind != "func":
                raise self.err(f"'{name}' is already declared as a variable", loc)
            if sym.builtin and header is None:
                raise self.err(f"'{name}' is a standard library function and cannot be redeclared", loc, hint="Choose a different name.")
            if not sym.builtin and not self._same_signature(sym.ty, ty):
                raise self.err(
                    f"conflicting types for '{name}': {T.type_str(sym.ty)} vs {T.type_str(ty)}",
                    loc,
                    hint="The prototype and the definition must have the same return and parameter types.",
                )
            return sym
        sym = self.new_symbol(name, "func", ty, "builtin" if header else "global", loc)
        sym.builtin = header is not None
        g[name] = sym
        return sym

    @staticmethod
    def _same_signature(a: T.FunctionType, b: T.FunctionType) -> bool:
        if a.ret != b.ret:
            return False
        if not a.prototyped or not b.prototyped:
            return True
        return a.params == b.params and a.variadic == b.variadic

    # ---------------------------------------------------------- declarations

    def decl_stmt(self, ds: A.DeclStmt, top_level: bool = False) -> None:
        for d in ds.decls:
            if isinstance(d, A.EnumConst):
                sym = self.new_symbol(d.name, "enum", T.INT, "global", d.loc)
                sym.value = d.value
                self.declare(sym)
            elif isinstance(d, A.FuncDecl):
                if not top_level:
                    # A prototype inside a function refers to the global function.
                    sym = self.declare_function(d.name, d.ty, d.loc, None)
                    self.scopes[-1].setdefault(d.name, sym)
                else:
                    self.declare_function(d.name, d.ty, d.loc, d.loc.header)
            elif isinstance(d, A.VarDecl):
                self.var_decl(d, top_level)

    def var_decl(self, d: A.VarDecl, top_level: bool) -> None:
        ty = d.ty
        if isinstance(ty, T.ArrayType) and ty.length is None:
            if d.init is None:
                if d.storage == "extern":
                    pass
                else:
                    raise self.err(f"the size of array '{d.name}' is missing", d.name_loc, hint="Give a size, e.g. int a[10], or an initializer, e.g. int a[] = {1, 2, 3}.")
            else:
                n = self.array_length_from_init(ty, d.init)
                ty = T.ArrayType(ty.elem, n)
                d.ty = ty
        if not ty.complete and d.storage != "extern":
            raise self.err(f"variable '{d.name}' has incomplete type '{T.type_str(ty)}'", d.name_loc, hint="A struct must be defined (with its members) before variables of that type can be created.")

        if top_level:
            if d.loc.header:
                if d.storage == "extern":
                    sym = self.new_symbol(d.name, "var", ty, "builtin", d.name_loc)
                    sym.builtin = True
                    self.scopes[0][d.name] = sym
                    d.sym = sym
                    self.builtin_globals.append(sym)
                return
            prev = self.scopes[0].get(d.name)
            if prev is not None and prev.kind == "var" and prev.ty == ty and prev.storage == "global":
                sym = prev
                if d.init is not None and prev.node is not None and prev.node.init is not None:
                    raise self.err(f"global variable '{d.name}' is initialized twice", d.name_loc)
            else:
                if prev is not None:
                    raise self.err(f"'{d.name}' is already declared (at line {prev.loc.line})", d.name_loc)
                sym = self.new_symbol(d.name, "var", ty, "global", d.name_loc)
                self.scopes[0][d.name] = sym
            d.sym = sym
            if d.storage == "extern" and d.init is None:
                return
            if sym.node is None or d.init is not None:
                if sym.node is not None and sym.node in self.globals:
                    self.globals.remove(sym.node)
                sym.node = d
                self.globals.append(d)
            if d.init is not None:
                self.check_constant_init(d.init)
            self.plan_init(d)
            return

        if d.storage == "extern":
            g = self.scopes[0].get(d.name)
            if g is None or g.kind != "var":
                raise self.err(f"extern variable '{d.name}' is not defined as a global", d.name_loc)
            self.scopes[-1][d.name] = g
            d.sym = g
            return
        storage = "static" if d.storage == "static" else "local"
        sym = self.new_symbol(d.name, "var", ty, storage, d.name_loc)
        sym.node = d
        sym.owner = self.func.name if self.func else None
        self.declare(sym)
        d.sym = sym
        if storage == "static":
            if d.init is not None:
                self.check_constant_init(d.init)
            self.globals.append(d)
        self.plan_init(d)

    def check_constant_init(self, init: A.Expr) -> None:
        bad = self._find_nonconstant(init)
        if bad is not None:
            raise self.err(
                "global and static variables can only be initialized with constant values",
                bad.loc,
                hint="Their initial value is fixed before the program starts. Assign the value inside main() instead.",
            )

    def _find_nonconstant(self, e) -> A.Node | None:
        if isinstance(e, A.InitList):
            for it in e.items:
                r = self._find_nonconstant(it.value)
                if r is not None:
                    return r
            return None
        if isinstance(e, (A.IntLit, A.FloatLit, A.StrLit, A.SizeofType, A.SizeofExpr)):
            return None
        if isinstance(e, A.Ident):
            return None  # enum constants are fine; variables are caught below via '&'
        if isinstance(e, (A.Call, A.Assign, A.Postfix)) or (isinstance(e, A.Unary) and e.op in ("++", "--", "*")):
            return e
        if isinstance(e, A.Unary):
            if e.op == "&":
                return None
            return self._find_nonconstant(e.operand)
        if isinstance(e, A.Binary):
            return self._find_nonconstant(e.left) or self._find_nonconstant(e.right)
        if isinstance(e, A.Cast):
            return self._find_nonconstant(e.expr)
        if isinstance(e, A.Cond):
            return self._find_nonconstant(e.cond) or self._find_nonconstant(e.then) or self._find_nonconstant(e.other)
        return e

    def array_length_from_init(self, ty: T.ArrayType, init) -> int:
        if isinstance(init, A.StrLit) and T.is_char_array(ty):
            return len(init.data) + 1
        if isinstance(init, A.InitList):
            if len(init.items) == 1 and isinstance(init.items[0].value, A.StrLit) and T.is_char_array(ty) and not init.items[0].designators:
                return len(init.items[0].value.data) + 1
            # Count top-level elements, honouring [index] designators and brace elision.
            idx = 0
            maxn = 0
            elem = ty.elem
            per = 1
            if T.is_aggregate(elem):
                per = self._scalar_count(elem)
            i = 0
            items = init.items
            while i < len(items):
                it = items[i]
                if it.designators and it.designators[0][0] == "index":
                    idx = self.const_value(it.designators[0][1], "an array index in an initializer")
                if T.is_aggregate(elem) and not isinstance(it.value, A.InitList) and not (isinstance(it.value, A.StrLit) and T.is_char_array(elem)):
                    i += per
                else:
                    i += 1
                idx += 1
                maxn = max(maxn, idx)
            if maxn == 0:
                raise self.err("an array cannot have size zero", init.loc)
            return maxn
        raise self.err("an array must be initialized with a list in braces, like {1, 2, 3}", init.loc)

    def _scalar_count(self, t: T.CType) -> int:
        if isinstance(t, T.ArrayType):
            return (t.length or 0) * self._scalar_count(t.elem)
        if isinstance(t, T.StructType):
            if t.is_union:
                return self._scalar_count(t.fields[0].type)
            return sum(self._scalar_count(f.type) for f in t.fields)
        return 1

    # Initializers are flattened into (offset, type, expr) writes.
    def plan_init(self, d: A.VarDecl) -> None:
        init = d.init
        if init is None:
            d.init_plan = []
            d.zero_fill = d.sym.storage in ("global", "static")
            return
        out: list = []
        self.init_one(d.ty, init, 0, out, d.name)
        d.init_plan = out
        d.zero_fill = isinstance(init, A.InitList) or d.sym.storage in ("global", "static") or (
            isinstance(init, A.StrLit) and T.is_char_array(d.ty)
        )

    def init_one(self, ty: T.CType, init, offset: int, out: list, name: str) -> None:
        if isinstance(init, A.InitList):
            if T.is_aggregate(ty):
                if (
                    T.is_char_array(ty)
                    and len(init.items) == 1
                    and isinstance(init.items[0].value, A.StrLit)
                    and not init.items[0].designators
                ):
                    self.init_one(ty, init.items[0].value, offset, out, name)
                    return
                used = self.init_aggregate(ty, init.items, 0, offset, out, name, braced=True)
                if used < len(init.items):
                    self.warn(f"too many values in the initializer of '{name}'; the extra ones are ignored", init.items[used].loc)
                return
            if not init.items:
                raise self.err("empty braces cannot initialize a single value", init.loc)
            if len(init.items) > 1:
                self.warn(f"too many values in the initializer of '{name}'", init.items[1].loc)
            self.init_one(ty, init.items[0].value, offset, out, name)
            return
        if isinstance(init, A.StrLit) and T.is_char_array(ty):
            init.ty = T.ArrayType(T.CHAR, len(init.data) + 1)
            if len(init.data) > ty.length:
                self.warn(
                    f"the string is longer than the array '{name}' ({len(init.data)} characters + '\\0' do not fit into {ty.length} bytes)",
                    init.loc,
                    hint="Make the array bigger. Characters that do not fit are cut off.",
                )
            out.append((offset, ty, init))
            return
        if isinstance(ty, T.ArrayType):
            raise self.err(
                f"array '{name}' must be initialized with a list in braces",
                init.loc,
                hint="Write e.g. int a[3] = {1, 2, 3};  Arrays cannot be copied with '='.",
            )
        e = self.rvalue(init)
        e = self.convert(e, ty, f"the initialization of '{name}'")
        out.append((offset, ty, e))

    def init_aggregate(self, ty: T.CType, items: list, i: int, offset: int, out: list, name: str, braced: bool) -> int:
        if isinstance(ty, T.ArrayType):
            n = ty.length
            idx = 0
            while i < len(items) and (n is None or idx < n or (braced and items[i].designators)):
                it = items[i]
                if it.designators:
                    if not braced:
                        return i
                    kind, val = it.designators[0]
                    if kind != "index":
                        raise self.err("a .member designator cannot initialize an array element", it.loc)
                    if len(it.designators) > 1:
                        raise self.err("nested designators are not supported", it.loc)
                    idx = self.const_value(val, "an array index in an initializer")
                    if idx < 0 or (n is not None and idx >= n):
                        raise self.err(f"index [{idx}] is outside the array '{name}' (valid: 0..{(n or 1) - 1})", it.loc)
                elif n is not None and idx >= n:
                    break
                sub = ty.elem
                pos = offset + idx * sub.size
                if T.is_aggregate(sub) and not isinstance(it.value, A.InitList) and not (isinstance(it.value, A.StrLit) and T.is_char_array(sub)) and not (isinstance(sub, T.StructType) and self._is_struct_expr(it.value, sub)):
                    i = self.init_aggregate(sub, items, i, pos, out, name, braced=False)
                else:
                    self.init_one(sub, it.value, pos, out, name)
                    i += 1
                idx += 1
            return i
        assert isinstance(ty, T.StructType)
        fields = ty.fields[:1] if ty.is_union else ty.fields
        fi = 0
        while i < len(items):
            it = items[i]
            if not it.designators and fi >= len(fields):
                break
            if it.designators:
                if not braced:
                    return i
                kind, val = it.designators[0]
                if kind != "field":
                    raise self.err("an [index] designator cannot initialize a struct member", it.loc)
                if len(it.designators) > 1:
                    raise self.err("nested designators are not supported", it.loc)
                f = ty.by_name.get(val)
                if f is None:
                    raise self.err(f"{T.type_str(ty)} has no member named '{val}'", it.loc)
                fi = ty.fields.index(f)
                fields = ty.fields
            f = fields[fi]
            pos = offset + f.offset
            sub = f.type
            if T.is_aggregate(sub) and not isinstance(it.value, A.InitList) and not (isinstance(it.value, A.StrLit) and T.is_char_array(sub)) and not (isinstance(sub, T.StructType) and self._is_struct_expr(it.value, sub)):
                i = self.init_aggregate(sub, items, i, pos, out, name, braced=False)
            else:
                self.init_one(sub, it.value, pos, out, name)
                i += 1
            fi += 1
            if ty.is_union:
                break
        return i

    def _is_struct_expr(self, e, st: T.StructType) -> bool:
        """Is ``e`` (unchecked) a variable of struct type ``st``? Used for brace elision."""
        if isinstance(e, A.Ident):
            sym = self.lookup(e.name)
            return sym is not None and sym.kind == "var" and sym.ty == st
        return False

    # ------------------------------------------------------------ statements

    def stmt(self, s: A.Stmt) -> None:
        if isinstance(s, A.ExprStmt):
            s.expr = self.expr_stmt_expr(s.expr)
        elif isinstance(s, A.DeclStmt):
            self.decl_stmt(s)
        elif isinstance(s, A.Compound):
            self.scopes.append({})
            for it in s.items:
                self.stmt(it)
            self.scopes.pop()
        elif isinstance(s, A.If):
            s.cond = self.condition(s.cond, "if")
            self.stmt(s.then)
            if s.other is not None:
                self.stmt(s.other)
        elif isinstance(s, A.While):
            s.cond = self.condition(s.cond, "while")
            self.loop_depth += 1
            self.stmt(s.body)
            self.loop_depth -= 1
        elif isinstance(s, A.DoWhile):
            self.loop_depth += 1
            self.stmt(s.body)
            self.loop_depth -= 1
            s.cond = self.condition(s.cond, "while")
        elif isinstance(s, A.For):
            self.scopes.append({})
            if s.init is not None:
                self.stmt(s.init)
            if s.cond is not None:
                s.cond = self.condition(s.cond, "for")
            if s.step is not None:
                s.step = self.expr_stmt_expr(s.step)
            self.loop_depth += 1
            self.stmt(s.body)
            self.loop_depth -= 1
            self.scopes.pop()
        elif isinstance(s, A.Switch):
            self.switch(s)
        elif isinstance(s, A.Case):
            raise self.err("'case' or 'default' outside of a switch", s.loc)
        elif isinstance(s, A.Break):
            if self.loop_depth == 0 and self.switch_depth == 0:
                raise self.err("'break' outside of a loop or switch", s.loc)
        elif isinstance(s, A.Continue):
            if self.loop_depth == 0:
                raise self.err("'continue' outside of a loop", s.loc)
        elif isinstance(s, A.Return):
            self.return_stmt(s)
        elif isinstance(s, A.Empty):
            pass
        else:  # pragma: no cover
            raise self.err(f"unsupported statement {type(s).__name__}", s.loc)

    def expr_stmt_expr(self, e: A.Expr) -> A.Expr:
        e = self.expr(e)
        if isinstance(e, (A.Binary,)) and e.op in ("==", "!=", "<", ">", "<=", ">=") and isinstance(_strip(e.left), A.Ident):
            if e.op == "==":
                self.warn("this comparison has no effect", e.loc, hint="Did you mean '=' (assignment) instead of '==' (comparison)?")
        return e

    def condition(self, e: A.Expr, what: str) -> A.Expr:
        if isinstance(e, A.Assign) and e.op == "=":
            self.warn(
                f"assignment used as the condition of '{what}'",
                e.loc,
                hint="'=' stores a value; '==' compares two values. Did you mean '=='?",
            )
        e = self.rvalue(e)
        if not T.is_scalar(e.ty):
            raise self.err(f"the condition of '{what}' must be a number or pointer, not '{T.type_str(e.ty)}'", e.loc)
        return e

    def switch(self, s: A.Switch) -> None:
        s.expr = self.rvalue(s.expr)
        if not T.is_integer(s.expr.ty):
            raise self.err("switch only works with integer values (int, char, enum ...)", s.expr.loc)
        s.expr = self.convert(s.expr, T.promote(s.expr.ty), "switch")
        body = s.body
        if not isinstance(body, A.Compound):
            body = A.Compound([body], body.loc, body.loc)
            s.body = body
        self.switch_depth += 1
        self.scopes.append({})
        for idx, item in enumerate(body.items):
            node = item
            while isinstance(node, A.Case):
                if node.expr is None:
                    if s.default_index is not None:
                        raise self.err("more than one 'default' in this switch", node.loc)
                    s.default_index = idx
                else:
                    v = self.const_value(self.expr(node.expr), "a case value")
                    v = s.expr.ty.wrap(v)
                    if v in s.table:
                        raise self.err(f"duplicate case value {v}", node.loc)
                    s.table[v] = idx
                    node.value = v
                node = node.stmt
            self._no_nested_cases(node)
            self.stmt(node)
        self.scopes.pop()
        self.switch_depth -= 1

    def _no_nested_cases(self, s) -> None:
        if isinstance(s, A.Case):
            raise self.err("case labels nested inside other statements are not supported", s.loc)
        for attr in ("then", "other", "body"):
            sub = getattr(s, attr, None)
            if isinstance(sub, A.Stmt) and not isinstance(s, A.Switch):
                self._no_nested_cases(sub)
        if isinstance(s, A.Compound):
            for it in s.items:
                self._no_nested_cases(it)

    def return_stmt(self, s: A.Return) -> None:
        fd = self.func
        ret = fd.ty.ret
        if s.expr is None:
            if not T.is_void(ret):
                self.warn(f"'return;' without a value in '{fd.name}', which should return {T.type_str(ret)}", s.loc)
            return
        e = self.rvalue(s.expr)
        if T.is_void(ret):
            if not T.is_void(e.ty):
                raise self.err(f"'{fd.name}' is a void function and cannot return a value", s.expr.loc, hint="Remove the value, or change the return type of the function.")
            s.expr = e
            return
        inner = _strip(e)
        target = None
        if isinstance(inner, A.Unary) and inner.op == "&":
            target = _strip(inner.operand)
        elif isinstance(e, A.Decay) and isinstance(_strip(e.expr), A.Ident):
            target = _strip(e.expr)
        if isinstance(target, A.Ident) and target.sym is not None and target.sym.storage in ("local", "param"):
            self.warn(
                f"returning the address of local variable '{target.name}'",
                s.expr.loc,
                hint=f"'{target.name}' lives in {fd.name}()'s stack frame, which is destroyed when the function returns. The caller gets a dangling pointer. Use malloc() or a static variable instead.",
                kind="dangling",
            )
        s.expr = self.convert(e, ret, "the return value")

    # ----------------------------------------------------------- expressions

    def rvalue(self, e: A.Expr) -> A.Expr:
        return self.decay(self.expr(e))

    def decay(self, e: A.Expr) -> A.Expr:
        """Apply array-to-pointer / function-to-pointer decay to a checked expression."""
        t = e.ty
        if isinstance(t, T.ArrayType):
            return A.Decay(e, T.PointerType(t.elem))
        if isinstance(t, T.FunctionType):
            if isinstance(e, A.Ident) and e.sym is not None and e.sym.builtin:
                raise self.err(f"library function '{e.name}' can only be called, not used as a value", e.loc)
            return A.Decay(e, T.PointerType(t))
        return e

    def convert(self, e: A.Expr, to: T.CType, ctx: str) -> A.Expr:
        src = e.ty
        if src == to and type(src) is type(to):
            return e
        if T.is_arith(to) and T.is_arith(src):
            pass
        elif isinstance(to, T.IntType) and to.rank == 0 and T.is_pointer(src):
            pass
        elif isinstance(to, T.PointerType):
            if isinstance(src, T.PointerType):
                a, b = to.target, src.target
                if not (a == b or T.is_void(a) or T.is_void(b) or self._compatible_qual(a, b)):
                    self.warn(
                        f"incompatible pointer types in {ctx}: '{T.type_str(src)}' is converted to '{T.type_str(to)}'",
                        e.loc,
                        hint="The pointers point to different types. If this is intended, write an explicit cast.",
                    )
            elif T.is_integer(src):
                if not _is_null_constant(e):
                    hint = "Pointers hold addresses. Use '&variable' to get the address of a variable."
                    raise self.err(f"cannot use an integer ({T.type_str(src)}) as a pointer ({T.type_str(to)}) in {ctx}", e.loc, hint=hint)
            else:
                raise self.err(f"cannot convert '{T.type_str(src)}' to '{T.type_str(to)}' in {ctx}", e.loc)
        elif T.is_integer(to) and T.is_pointer(src):
            hint = "A pointer holds an address. Use '*pointer' to get the value it points to."
            if isinstance(src.target, T.IntType) and src.target.size == 1:
                hint = "Strings are pointers to char. To convert text to a number use atoi(); to compare strings use strcmp()."
            raise self.err(f"cannot use a pointer ({T.type_str(src)}) as an integer ({T.type_str(to)}) in {ctx}", e.loc, hint=hint)
        elif isinstance(to, T.StructType) or isinstance(src, T.StructType):
            raise self.err(f"cannot convert '{T.type_str(src)}' to '{T.type_str(to)}' in {ctx}", e.loc)
        elif T.is_void(src):
            raise self.err(f"a void expression has no value and cannot be used in {ctx}", e.loc)
        elif T.is_void(to):
            pass
        else:
            raise self.err(f"cannot convert '{T.type_str(src)}' to '{T.type_str(to)}' in {ctx}", e.loc)
        c = A.Cast(to, e, e.loc, implicit=True)
        return c

    @staticmethod
    def _compatible_qual(a: T.CType, b: T.CType) -> bool:
        return isinstance(a, T.IntType) and isinstance(b, T.IntType) and a.size == b.size == 1

    def expr(self, e: A.Expr) -> A.Expr:
        m = getattr(self, "e_" + type(e).__name__)
        return m(e)

    def e_IntLit(self, e):
        return e

    def e_FloatLit(self, e):
        return e

    def e_StrLit(self, e):
        e.ty = T.ArrayType(T.CHAR, len(e.data) + 1)
        e.index = self.literal(e.data)
        return e

    def e_Ident(self, e):
        sym = self.lookup(e.name)
        if sym is None:
            raise self.undeclared(e.name, e.loc, is_call=False)
        if sym.kind == "enum":
            return A.IntLit(sym.value, T.INT, e.loc, e.name)
        if sym.builtin and sym.kind == "func" and sym.name.startswith("__"):
            raise self.undeclared(e.name, e.loc, is_call=False)
        e.sym = sym
        e.ty = sym.ty
        sym.uses.append(e.loc)
        return e

    def e_Decay(self, e):
        return e

    def e_Cast(self, e):
        if e.implicit:
            return e
        inner = self.rvalue(e.expr)
        e.expr = inner
        to, src = e.ty, inner.ty
        if T.is_void(to):
            return e
        if isinstance(to, (T.ArrayType, T.FunctionType)):
            raise self.err(f"cannot cast to '{T.type_str(to)}'", e.loc)
        if isinstance(to, T.StructType) or isinstance(src, T.StructType):
            raise self.err("structs cannot be cast", e.loc)
        if T.is_void(src):
            raise self.err("a void expression has no value", inner.loc)
        if (T.is_float(to) and T.is_pointer(src)) or (T.is_pointer(to) and T.is_float(src)):
            raise self.err("pointers and floating-point numbers cannot be converted into each other", e.loc)
        if T.is_pointer(to) and T.is_integer(src) and src.size < 8 and not _is_null_constant(inner):
            self.warn(f"casting {T.a_type(src)} to a pointer", e.loc, hint="Integers are rarely valid addresses.")
        return e

    def e_SizeofType(self, e):
        t = e.target
        if isinstance(t, T.FunctionType):
            raise self.err("sizeof cannot be applied to a function type", e.loc)
        if not t.complete:
            raise self.err(f"sizeof cannot be applied to incomplete type '{T.type_str(t)}'", e.loc)
        return A.IntLit(t.size, T.SIZE_T, e.loc, f"sizeof({T.type_str(t)})")

    def e_SizeofExpr(self, e):
        inner = self.expr(e.expr)
        t = inner.ty
        if isinstance(t, T.FunctionType):
            raise self.err("sizeof cannot be applied to a function", e.loc)
        if not t.complete:
            raise self.err(f"sizeof cannot be applied to incomplete type '{T.type_str(t)}'", e.loc)
        return A.IntLit(t.size, T.SIZE_T, e.loc, f"sizeof({A.expr_str(inner)})")

    def e_InitList(self, e):
        raise self.err("a brace list {...} can only be used to initialize a variable in its declaration", e.loc, hint="Arrays and structs cannot be assigned with '=' after their declaration.")

    def e_Unary(self, e):
        op = e.op
        if op == "&":
            inner = self.expr(e.operand)
            e.operand = inner
            if isinstance(inner, A.Ident) and inner.sym is not None and inner.sym.kind == "func":
                if inner.sym.builtin:
                    raise self.err(f"taking the address of library function '{inner.name}' is not supported", e.loc)
                e.ty = T.PointerType(inner.ty)
                return e
            if not _is_lvalue(inner):
                raise self.err(
                    f"cannot take the address of '{A.expr_str(inner)}'",
                    e.loc,
                    hint="'&' needs something stored in memory, like a variable, an array element or a struct member - not a temporary value.",
                )
            e.ty = T.PointerType(inner.ty)
            return e
        if op == "*":
            inner = self.rvalue(e.operand)
            e.operand = inner
            t = inner.ty
            if not isinstance(t, T.PointerType):
                raise self.err(
                    f"cannot dereference '{A.expr_str(inner)}' because it is not a pointer (its type is {T.type_str(t)})",
                    e.loc,
                    hint="'*' follows a pointer to the value it points to. Only pointers can be dereferenced.",
                )
            if T.is_void(t.target):
                raise self.err("cannot dereference a 'void *' pointer", e.loc, hint="Cast it to a pointer of the right type first, e.g. *(int *)p.")
            if not t.target.complete and not isinstance(t.target, T.FunctionType):
                raise self.err(f"cannot dereference a pointer to incomplete type '{T.type_str(t.target)}'", e.loc)
            e.ty = t.target
            return e
        if op in ("++", "--"):
            return self.incdec(e)
        inner = self.rvalue(e.operand)
        t = inner.ty
        if op == "!":
            if not T.is_scalar(t):
                raise self.err(f"'!' needs a number or pointer, not '{T.type_str(t)}'", e.loc)
            e.operand = inner
            e.ty = T.INT
            return e
        if op == "~":
            if not T.is_integer(t):
                raise self.err(f"'~' needs an integer, not '{T.type_str(t)}'", e.loc)
        elif not T.is_arith(t):
            raise self.err(f"unary '{op}' needs a number, not '{T.type_str(t)}'", e.loc)
        pt = T.promote(t)
        e.operand = self.convert(inner, pt, "an expression")
        e.ty = pt
        return e

    def incdec(self, e):
        inner = self.expr(e.operand)
        self.require_modifiable(inner, f"'{e.op}'")
        t = inner.ty
        if not (T.is_arith(t) or (T.is_pointer(t) and t.target.complete)):
            raise self.err(f"'{e.op}' needs a number or pointer variable, not '{T.type_str(t)}'", e.loc)
        e.operand = inner
        e.ty = t
        return e

    e_Postfix = incdec

    def require_modifiable(self, e: A.Expr, what: str) -> None:
        if not _is_lvalue(e):
            raise self.err(
                f"{what} needs a variable (or other memory location) on its left side, but '{A.expr_str(e)}' is just a value",
                e.loc,
                hint="Only things stored in memory - variables, array elements, struct members, *pointer - can be assigned to.",
            )
        if isinstance(e.ty, T.ArrayType):
            raise self.err(
                f"cannot assign to the array '{A.expr_str(e)}'",
                e.loc,
                hint="Arrays cannot be assigned as a whole. Copy element by element, or use strcpy() for strings and memcpy() for other data.",
            )
        if isinstance(e, A.StrLit):
            raise self.err("cannot assign to a string literal", e.loc)

    def e_Binary(self, e):
        op = e.op
        left = self.rvalue(e.left)
        right = self.rvalue(e.right)
        lt, rt = left.ty, right.ty
        if T.is_void(lt) or T.is_void(rt):
            raise self.err(f"a void value cannot be used with '{op}'", e.loc)
        if op in ("&&", "||"):
            for side in (left, right):
                if not T.is_scalar(side.ty):
                    raise self.err(f"'{op}' needs numbers or pointers, not '{T.type_str(side.ty)}'", side.loc)
            e.left, e.right, e.ty, e.mode = left, right, T.INT, "logic"
            return e
        if op in ("+", "-") and (T.is_pointer(lt) or T.is_pointer(rt)):
            return self.pointer_arith(e, left, right)
        if op in ("==", "!=", "<", ">", "<=", ">=") and (T.is_pointer(lt) or T.is_pointer(rt)):
            return self.pointer_compare(e, left, right)
        if isinstance(lt, T.StructType) or isinstance(rt, T.StructType):
            raise self.err(
                f"structs cannot be used with '{op}'",
                e.loc,
                hint="Compare or combine the individual members instead (e.g. a.x == b.x).",
            )
        if op in ("%", "<<", ">>", "&", "|", "^"):
            if not (T.is_integer(lt) and T.is_integer(rt)):
                bad = left if not T.is_integer(lt) else right
                raise self.err(f"'{op}' only works with integers, but '{A.expr_str(bad)}' has type {T.type_str(bad.ty)}", bad.loc, hint="For the remainder of floating-point numbers use fmod() from <math.h>." if op == "%" else None)
        elif not (T.is_arith(lt) and T.is_arith(rt)):
            bad = left if not T.is_arith(lt) else right
            raise self.err(f"'{op}' cannot be used with '{T.type_str(bad.ty)}'", bad.loc)
        if op in ("<<", ">>"):
            pt = T.promote(lt)
            e.left = self.convert(left, pt, "a shift")
            e.right = self.convert(right, T.promote(rt), "a shift")
            e.ty, e.mode = pt, "shift"
            return e
        ct = T.arith_common(lt, rt)
        e.left = self.convert(left, ct, "an expression")
        e.right = self.convert(right, ct, "an expression")
        if op in ("==", "!=", "<", ">", "<=", ">="):
            e.ty, e.mode = T.INT, "cmp"
            if op != "==" and op != "!=" and T.is_integer(ct) and not ct.signed:
                for side, other in ((left, lt), (right, rt)):
                    if isinstance(side, A.Unary) and side.op == "-" and isinstance(side.operand, A.IntLit):
                        self.warn("comparing an unsigned value with a negative number", e.loc, hint="The negative number is converted to a huge unsigned value, so the result is probably not what you expect.")
            return e
        if op in ("/", "%") and isinstance(right, A.IntLit) and right.value == 0 and T.is_integer(ct):
            self.warn("division by zero", right.loc)
        e.ty, e.mode = ct, "arith"
        if op == "/" and T.is_integer(ct) and isinstance(_strip(left), A.IntLit) and isinstance(_strip(right), A.IntLit):
            lv, rv = _strip(left).value, _strip(right).value
            if rv and lv % rv:
                self.warn(f"integer division {lv}/{rv} gives {int(lv / rv)}, not {lv / rv:g}", e.loc, hint="Dividing two integers drops the fractional part. Write 1.0/2 (or cast to double) for a floating-point result.")
        return e

    def pointer_arith(self, e, left, right):
        lt, rt = left.ty, right.ty
        op = e.op
        if T.is_pointer(lt) and T.is_pointer(rt):
            if op == "+":
                raise self.err("two pointers cannot be added", e.loc, hint="You can subtract pointers (to get the distance between them) or add an integer to a pointer.")
            if not (lt.target == rt.target or self._compatible_qual(lt.target, rt.target)):
                raise self.err(f"cannot subtract pointers to different types ('{T.type_str(lt)}' and '{T.type_str(rt)}')", e.loc)
            self._need_complete_target(lt, e.loc)
            e.left, e.right, e.ty, e.mode = left, right, T.PTRDIFF_T, "ptr_diff"
            return e
        if T.is_pointer(rt):
            if op == "-":
                raise self.err("cannot subtract a pointer from an integer", e.loc)
            left, right = right, left
            lt, rt = rt, lt
        if not T.is_integer(rt):
            raise self.err(f"only integers can be added to a pointer, not '{T.type_str(rt)}'", right.loc)
        self._need_complete_target(lt, e.loc)
        e.left = left
        e.right = self.convert(right, T.LONG, "pointer arithmetic")
        e.ty, e.mode = lt, ("ptr_add" if op == "+" else "ptr_sub")
        return e

    def _need_complete_target(self, pt: T.PointerType, loc: Loc) -> None:
        tgt = pt.target
        if T.is_void(tgt):
            raise self.err("pointer arithmetic on 'void *' is not allowed", loc, hint="Cast to 'char *' to move by bytes, or to the real element type.")
        if not tgt.complete or isinstance(tgt, T.FunctionType):
            raise self.err(f"pointer arithmetic needs a complete element type, not '{T.type_str(tgt)}'", loc)

    def pointer_compare(self, e, left, right):
        lt, rt = left.ty, right.ty
        if T.is_pointer(lt) and T.is_pointer(rt):
            if not (lt.target == rt.target or T.is_void(lt.target) or T.is_void(rt.target) or self._compatible_qual(lt.target, rt.target)):
                self.warn(f"comparing pointers of different types ('{T.type_str(lt)}' and '{T.type_str(rt)}')", e.loc)
        elif T.is_pointer(lt):
            if not _is_null_constant(right):
                if T.is_integer(rt):
                    raise self.err(f"comparing a pointer with an integer: '{A.expr_str(left)}' is a pointer", e.loc, hint="Did you mean to compare the value it points to (*p), or to compare with NULL?")
                raise self.err("cannot compare a pointer with this value", e.loc)
            right = self.convert(right, lt, "a comparison")
        else:
            if not _is_null_constant(left):
                if T.is_integer(lt):
                    raise self.err(f"comparing an integer with a pointer: '{A.expr_str(right)}' is a pointer", e.loc, hint="Did you mean to compare the value it points to (*p), or to compare with NULL?")
                raise self.err("cannot compare a pointer with this value", e.loc)
            left = self.convert(left, rt, "a comparison")
        e.left, e.right, e.ty, e.mode = left, right, T.INT, "ptr_cmp"
        return e

    def e_Assign(self, e):
        target = self.expr(e.target)
        self.require_modifiable(target, "assignment")
        e.target = target
        tt = target.ty
        if isinstance(tt, T.FunctionType):
            raise self.err("cannot assign to a function", e.loc)
        if e.op == "=":
            value = self.rvalue(e.value)
            if isinstance(tt, T.StructType):
                if value.ty != tt:
                    raise self.err(f"cannot assign '{T.type_str(value.ty)}' to '{T.type_str(tt)}'", e.loc)
                e.value = value
            else:
                e.value = self.convert(value, tt, f"the assignment to '{A.expr_str(target)}'")
            e.ty = tt
            return e
        op = e.op[:-1]
        value = self.rvalue(e.value)
        vt = value.ty
        if T.is_pointer(tt) and op in ("+", "-"):
            if not T.is_integer(vt):
                raise self.err(f"'{e.op}' on a pointer needs an integer, not '{T.type_str(vt)}'", value.loc)
            self._need_complete_target(tt, e.loc)
            e.value = self.convert(value, T.LONG, "pointer arithmetic")
            e.op_ty, e.mode, e.ty = tt, ("ptr_add" if op == "+" else "ptr_sub"), tt
            return e
        if op in ("%", "<<", ">>", "&", "|", "^"):
            if not (T.is_integer(tt) and T.is_integer(vt)):
                raise self.err(f"'{e.op}' only works with integers", e.loc)
        elif not (T.is_arith(tt) and T.is_arith(vt)):
            raise self.err(f"'{e.op}' cannot be used with '{T.type_str(tt if not T.is_arith(tt) else vt)}'", e.loc)
        if op in ("<<", ">>"):
            e.op_ty, e.mode = T.promote(tt), "shift"
            e.value = self.convert(value, T.promote(vt), "a shift")
        else:
            ct = T.arith_common(tt, vt)
            e.op_ty, e.mode = ct, "arith"
            e.value = self.convert(value, ct, "an expression")
        e.ty = tt
        return e

    def e_Cond(self, e):
        c = self.rvalue(e.cond)
        if not T.is_scalar(c.ty):
            raise self.err("the condition of '?:' must be a number or pointer", c.loc)
        a = self.rvalue(e.then)
        b = self.rvalue(e.other)
        at, bt = a.ty, b.ty
        if T.is_arith(at) and T.is_arith(bt):
            ct = T.arith_common(at, bt)
            a, b = self.convert(a, ct, "?:"), self.convert(b, ct, "?:")
        elif T.is_pointer(at) and T.is_pointer(bt):
            ct = at if not T.is_void(at.target) else bt
        elif T.is_pointer(at) and _is_null_constant(b):
            ct = at
            b = self.convert(b, ct, "?:")
        elif T.is_pointer(bt) and _is_null_constant(a):
            ct = bt
            a = self.convert(a, ct, "?:")
        elif T.is_void(at) and T.is_void(bt):
            ct = T.VOID
        elif isinstance(at, T.StructType) and at == bt:
            ct = at
        else:
            raise self.err(f"the two results of '?:' have incompatible types ('{T.type_str(at)}' and '{T.type_str(bt)}')", e.loc)
        e.cond, e.then, e.other, e.ty = c, a, b, ct
        return e

    def e_Comma(self, e):
        e.left = self.expr(e.left)
        e.right = self.rvalue(e.right)
        e.ty = e.right.ty
        return e

    def e_Index(self, e):
        base = self.rvalue(e.base)
        idx = self.rvalue(e.index)
        if T.is_integer(base.ty) and T.is_pointer(idx.ty):
            base, idx = idx, base
        if not T.is_pointer(base.ty):
            raise self.err(
                f"'{A.expr_str(base)}' is not an array or pointer, so it cannot be indexed with [...]",
                e.loc,
                hint=f"Its type is {T.type_str(base.ty)}.",
            )
        if not T.is_integer(idx.ty):
            raise self.err(f"an array index must be an integer, not '{T.type_str(idx.ty)}'", idx.loc)
        self._need_complete_target(base.ty, e.loc)
        e.base = base
        e.index = self.convert(idx, T.LONG, "an index")
        e.ty = base.ty.target
        return e

    def e_Member(self, e):
        obj = self.expr(e.obj)
        if e.arrow:
            obj = self.decay(obj)
            t = obj.ty
            if isinstance(t, T.StructType):
                raise self.err(
                    f"'{A.expr_str(obj)}' is a struct, not a pointer: use '.' instead of '->'",
                    e.loc,
                    hint=f"Write {A.expr_str(obj)}.{e.name}",
                )
            if not (T.is_pointer(t) and isinstance(t.target, T.StructType)):
                raise self.err(f"'->' needs a pointer to a struct, but '{A.expr_str(obj)}' has type {T.type_str(t)}", e.loc)
            st = t.target
        else:
            t = obj.ty
            if T.is_pointer(t) and isinstance(t.target, T.StructType):
                raise self.err(
                    f"'{A.expr_str(obj)}' is a pointer to a struct: use '->' instead of '.'",
                    e.loc,
                    hint=f"Write {A.expr_str(obj)}->{e.name}  (which means (*{A.expr_str(obj)}).{e.name})",
                )
            if not isinstance(t, T.StructType):
                raise self.err(f"'{A.expr_str(obj)}' is not a struct (its type is {T.type_str(t)}), so it has no members", e.loc)
            st = t
        if not st.complete:
            raise self.err(f"{T.type_str(st)} is incomplete (its members are not known here)", e.loc)
        f = st.by_name.get(e.name)
        if f is None:
            close = difflib.get_close_matches(e.name, list(st.by_name), n=1, cutoff=0.6)
            hint = f"Did you mean '{close[0]}'?" if close else f"Its members are: {', '.join(st.by_name)}."
            raise self.err(f"{T.type_str(st)} has no member named '{e.name}'", e.loc, hint=hint)
        e.obj, e.field, e.ty = obj, f, f.type
        return e

    def e_Call(self, e):
        func = e.func
        fsym = None
        if isinstance(func, A.Ident):
            sym = self.lookup(func.name)
            if sym is None:
                raise self.undeclared(func.name, func.loc, is_call=True)
            if sym.kind == "func":
                fsym = sym
                func.sym = sym
                func.ty = sym.ty
                sym.uses.append(func.loc)
                ft = sym.ty
            else:
                func = self.rvalue(func)
                ft = None
        else:
            func = self.rvalue(func)
            ft = None
        if ft is None:
            t = func.ty
            if T.is_pointer(t) and isinstance(t.target, T.FunctionType):
                ft = t.target
            else:
                raise self.err(f"'{A.expr_str(func)}' is not a function and cannot be called", e.loc, hint=f"Its type is {T.type_str(t)}.")
        e.func, e.fsym = func, fsym
        name = fsym.name if fsym is not None else A.expr_str(func)
        params = ft.params
        nargs = len(e.args)
        if ft.prototyped:
            if nargs < len(params) or (nargs > len(params) and not ft.variadic):
                raise self.err(
                    f"{name}() expects {len(params)} argument{'s' if len(params) != 1 else ''} but {nargs} {'were' if nargs != 1 else 'was'} given",
                    e.loc,
                    hint=f"Its declaration is: {T.type_str(ft.ret)} {name}({', '.join(T.type_str(p) for p in params) or 'void'}{', ...' if ft.variadic else ''})",
                )
        args = []
        for i, a in enumerate(e.args):
            a = self.rvalue(a)
            if T.is_void(a.ty):
                raise self.err("a void value cannot be passed as an argument", a.loc)
            if i < len(params) and ft.prototyped:
                pt = params[i]
                if isinstance(pt, T.StructType):
                    if a.ty != pt:
                        raise self.err(f"argument {i + 1} of {name}() must be '{T.type_str(pt)}', not '{T.type_str(a.ty)}'", a.loc)
                else:
                    a = self.convert(a, pt, f"argument {i + 1} of {name}()")
            else:
                if T.is_float(a.ty):
                    a = self.convert(a, T.DOUBLE, "an argument")
                elif T.is_integer(a.ty) and a.ty.rank < 3:
                    a = self.convert(a, T.INT, "an argument")
            args.append(a)
        e.args = args
        e.ty = ft.ret
        if fsym is not None and fsym.builtin:
            if fsym.name in INPUT_FUNCTIONS:
                self.reads_stdin = True
            if fsym.name in PRINTF_FMT_INDEX:
                self.check_format(e, PRINTF_FMT_INDEX[fsym.name], scanf=False)
            elif fsym.name in SCANF_FMT_INDEX:
                self.check_format(e, SCANF_FMT_INDEX[fsym.name], scanf=True)
            elif fsym.name == "free" and args:
                inner = _strip(args[0])
                if isinstance(inner, A.Unary) and inner.op == "&":
                    self.warn("free() is given the address of a variable", args[0].loc, hint="free() only releases memory that came from malloc/calloc/realloc. Pass the pointer itself, not its address.")
        return e

    # ---------------------------------------------------------- format check

    def check_format(self, call: A.Call, fmt_index: int, scanf: bool) -> None:
        if len(call.args) <= fmt_index:
            return
        fmt = _strip(call.args[fmt_index])
        if not isinstance(fmt, A.StrLit):
            return
        text = fmt.data.decode("latin-1")
        args = call.args[fmt_index + 1 :]
        name = call.fsym.name
        ai = 0
        for m in _FMT_RE.finditer(text):
            flags, width, prec, length, conv = m.groups()
            if conv is None:
                self.warn(f"incomplete format specifier '%' in {name}()", fmt.loc, hint="Write '%%' to print a percent sign.")
                return
            if conv == "%":
                continue
            if not scanf:
                for star in (width, prec):
                    if star == "*":
                        if ai < len(args) and not T.is_integer(args[ai].ty):
                            self.warn(f"'*' in '{m.group()}' needs an int argument", args[ai].loc)
                        ai += 1
            spec = m.group()
            if ai >= len(args):
                self.warn(
                    f"the format '{spec}' in {name}() has no matching argument",
                    call.loc,
                    hint="Every % conversion in the format string needs one value after the format.",
                    kind="format",
                )
                return
            arg = args[ai]
            ai += 1
            if scanf:
                self.check_scanf_arg(spec, conv, length, arg, name)
            else:
                self.check_printf_arg(spec, conv, length, arg, name)
        if ai < len(args):
            self.warn(
                f"{name}() got more arguments than its format string uses",
                args[ai].loc,
                hint="Each value needs a % conversion in the format string, e.g. %d for an int.",
                kind="format",
            )

    def check_printf_arg(self, spec: str, conv: str, length: str | None, arg: A.Expr, name: str) -> None:
        t = arg.ty
        orig = _strip(arg)
        ts = T.type_str(orig.ty if orig.ty is not None else t)
        if conv in "di" or conv in "uoxX":
            if T.is_float(t):
                self.warn(f"'{spec}' expects an integer, but the argument is {T.a_type(t)}", arg.loc, hint="Use %f to print a floating-point number, or cast the value to (int).", kind="format")
            elif T.is_pointer(t):
                hint = "Use %s to print a string." if isinstance(t.target, T.IntType) and t.target.size == 1 else "Use %p to print an address, or dereference the pointer (*p) to print the value."
                self.warn(f"'{spec}' expects an integer, but the argument is a pointer ({T.type_str(t)})", arg.loc, hint=hint, kind="format")
            elif T.is_integer(t):
                want = 8 if length in ("l", "ll", "z", "j", "t") else 4
                have = t.size if t.rank >= 3 else 4
                if want != have and length not in ("h", "hh"):
                    fix = {"d": "%ld", "i": "%li", "u": "%lu", "x": "%lx", "X": "%lX", "o": "%lo"}.get(conv, "%ld") if have == 8 else f"%{conv}"
                    self.warn(f"'{spec}' does not match the argument type {ts}", arg.loc, hint=f"Use {fix} for this value.", kind="format")
        elif conv == "c":
            if not T.is_integer(t):
                self.warn(f"'%c' expects a character, but the argument is {T.type_str(t)}", arg.loc, hint="Use %s for strings." if T.is_pointer(t) else None, kind="format")
        elif conv in "fFeEgGaA":
            if T.is_integer(t):
                self.warn(f"'{spec}' expects a double, but the argument is an integer ({ts})", arg.loc, hint="Use %d for integers, or convert with (double)value.", kind="format")
            elif not T.is_float(t):
                self.warn(f"'{spec}' expects a double, but the argument has type {T.type_str(t)}", arg.loc, kind="format")
        elif conv == "s":
            if not (T.is_pointer(t) and isinstance(t.target, T.IntType) and t.target.size == 1):
                hint = "Use %c to print a single character." if T.is_integer(t) else None
                self.warn(f"'%s' expects a string (char *), but the argument has type {ts}", arg.loc, hint=hint, kind="format")
        elif conv == "p":
            if not T.is_pointer(t):
                self.warn(f"'%p' expects a pointer, but the argument has type {ts}", arg.loc, kind="format")
        elif conv == "n":
            self.warn("'%n' is not supported by stacksim and is ignored", arg.loc, kind="format")
        else:
            self.warn(f"unknown conversion '{spec}' in the format string", arg.loc, kind="format")

    def check_scanf_arg(self, spec: str, conv: str, length: str | None, arg: A.Expr, name: str) -> None:
        t = arg.ty
        if not T.is_pointer(t):
            inner = _strip(arg)
            hint = f"Write &{A.expr_str(inner)} so that {name}() knows where to store the value." if isinstance(inner, (A.Ident, A.Member, A.Index)) else None
            self.warn(f"{name}() needs the address of a variable for '{spec}', but got {T.a_type(t)}", arg.loc, hint=hint, kind="format")
            return
        tgt = t.target
        if conv in "di" or conv in "uoxX":
            want = 8 if length in ("l", "ll", "z", "j") else (2 if length == "h" else (1 if length == "hh" else 4))
            if not T.is_integer(tgt) or tgt.size != want:
                self.warn(f"'{spec}' stores {'an int' if want == 4 else f'a {want}-byte integer'}, but the argument points to {T.type_str(tgt)}", arg.loc, hint="Use %lf for double, %f for float, %ld for long, %c for char." , kind="format")
        elif conv in "fFeEgGaA":
            want = 8 if length in ("l", "L") else 4
            if not T.is_float(tgt) or tgt.size != want:
                hint = "For a double variable use %lf in scanf (printf uses %f for both)." if T.is_float(tgt) and tgt.size == 8 else "Use the conversion that matches the variable's type."
                self.warn(f"'{spec}' stores a {'double' if want == 8 else 'float'}, but the argument points to {T.type_str(tgt)}", arg.loc, hint=hint, kind="format")
        elif conv in "cs[":
            if not (isinstance(tgt, T.IntType) and tgt.size == 1) and not (isinstance(tgt, T.ArrayType) and T.is_char_array(tgt)):
                self.warn(f"'{spec}' stores characters, but the argument points to {T.type_str(tgt)}", arg.loc, kind="format")
        else:
            self.warn(f"conversion '{spec}' is not supported by stacksim's scanf", arg.loc, kind="format")


def check(tu: A.TranslationUnit, headers: list[str]) -> Program:
    return Sema(headers).check(tu)

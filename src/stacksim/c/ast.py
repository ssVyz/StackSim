"""AST node classes. The parser builds them; the checker (sema) fills in types,
symbols and implicit conversions; the interpreter executes them."""

from __future__ import annotations

from .ctype import CType
from .errors import Loc


class Node:
    __slots__ = ("loc",)


# ---------------------------------------------------------------- expressions


class Expr(Node):
    __slots__ = ("ty",)


class IntLit(Expr):
    __slots__ = ("value", "text")

    def __init__(self, value: int, ty: CType, loc: Loc, text: str | None = None):
        self.value, self.ty, self.loc, self.text = value, ty, loc, text


class FloatLit(Expr):
    __slots__ = ("value", "text")

    def __init__(self, value: float, ty: CType, loc: Loc, text: str | None = None):
        self.value, self.ty, self.loc, self.text = value, ty, loc, text


class StrLit(Expr):
    __slots__ = ("data", "index")

    def __init__(self, data: bytes, loc: Loc):
        self.data, self.loc, self.ty, self.index = data, loc, None, -1


class Ident(Expr):
    __slots__ = ("name", "sym")

    def __init__(self, name: str, loc: Loc):
        self.name, self.loc, self.ty, self.sym = name, loc, None, None


class Unary(Expr):
    __slots__ = ("op", "operand")

    def __init__(self, op: str, operand: Expr, loc: Loc):
        self.op, self.operand, self.loc, self.ty = op, operand, loc, None


class Postfix(Expr):
    __slots__ = ("op", "operand")

    def __init__(self, op: str, operand: Expr, loc: Loc):
        self.op, self.operand, self.loc, self.ty = op, operand, loc, None


class Binary(Expr):
    # mode (set by sema): arith, shift, cmp, logic, ptr_add, ptr_sub, ptr_diff, ptr_cmp
    __slots__ = ("op", "left", "right", "mode")

    def __init__(self, op: str, left: Expr, right: Expr, loc: Loc):
        self.op, self.left, self.right, self.loc, self.ty, self.mode = op, left, right, loc, None, ""


class Assign(Expr):
    # For compound assignment, op_ty is the type the arithmetic is done in and
    # mode is the Binary mode of the underlying operation.
    __slots__ = ("op", "target", "value", "op_ty", "mode")

    def __init__(self, op: str, target: Expr, value: Expr, loc: Loc):
        self.op, self.target, self.value, self.loc, self.ty = op, target, value, loc, None
        self.op_ty, self.mode = None, ""


class Cond(Expr):
    __slots__ = ("cond", "then", "other")

    def __init__(self, cond: Expr, then: Expr, other: Expr, loc: Loc):
        self.cond, self.then, self.other, self.loc, self.ty = cond, then, other, loc, None


class Comma(Expr):
    __slots__ = ("left", "right")

    def __init__(self, left: Expr, right: Expr, loc: Loc):
        self.left, self.right, self.loc, self.ty = left, right, loc, None


class Call(Expr):
    __slots__ = ("func", "args", "fsym")

    def __init__(self, func: Expr, args: list[Expr], loc: Loc):
        self.func, self.args, self.loc, self.ty, self.fsym = func, args, loc, None, None


class Index(Expr):
    __slots__ = ("base", "index")

    def __init__(self, base: Expr, index: Expr, loc: Loc):
        self.base, self.index, self.loc, self.ty = base, index, loc, None


class Member(Expr):
    __slots__ = ("obj", "name", "arrow", "field")

    def __init__(self, obj: Expr, name: str, arrow: bool, loc: Loc):
        self.obj, self.name, self.arrow, self.loc, self.ty, self.field = obj, name, arrow, loc, None, None


class Cast(Expr):
    __slots__ = ("expr", "implicit")

    def __init__(self, ty: CType, expr: Expr, loc: Loc, implicit: bool = False):
        self.ty, self.expr, self.loc, self.implicit = ty, expr, loc, implicit


class Decay(Expr):
    """Array-to-pointer or function-to-pointer conversion (inserted by sema)."""

    __slots__ = ("expr",)

    def __init__(self, expr: Expr, ty: CType):
        self.expr, self.ty, self.loc = expr, ty, expr.loc


class SizeofType(Expr):
    __slots__ = ("target",)

    def __init__(self, target: CType, loc: Loc):
        self.target, self.loc, self.ty = target, loc, None


class SizeofExpr(Expr):
    __slots__ = ("expr",)

    def __init__(self, expr: Expr, loc: Loc):
        self.expr, self.loc, self.ty = expr, loc, None


class InitItem:
    __slots__ = ("designators", "value", "loc")

    def __init__(self, designators: list, value, loc: Loc):
        self.designators = designators  # list of ("field", name) / ("index", Expr)
        self.value = value  # Expr or InitList
        self.loc = loc


class InitList(Expr):
    __slots__ = ("items",)

    def __init__(self, items: list[InitItem], loc: Loc):
        self.items, self.loc, self.ty = items, loc, None


# ----------------------------------------------------------------- statements


class Stmt(Node):
    __slots__ = ()


class VarDecl(Node):
    # init_plan (set by sema): list of (offset, type, expr) writes; zero_fill: bool
    __slots__ = ("name", "ty", "init", "storage", "sym", "init_plan", "zero_fill", "name_loc")

    def __init__(self, name: str, ty: CType, init, storage: str | None, loc: Loc, name_loc: Loc | None = None):
        self.name, self.ty, self.init, self.storage, self.loc = name, ty, init, storage, loc
        self.sym = None
        self.init_plan = None
        self.zero_fill = False
        self.name_loc = name_loc or loc


class EnumConst(Node):
    __slots__ = ("name", "value", "ty")

    def __init__(self, name: str, value: int, ty: CType, loc: Loc):
        self.name, self.value, self.ty, self.loc = name, value, ty, loc


class FuncDecl(Node):
    __slots__ = ("name", "ty", "storage")

    def __init__(self, name: str, ty: CType, storage: str | None, loc: Loc):
        self.name, self.ty, self.storage, self.loc = name, ty, storage, loc


class DeclStmt(Stmt):
    __slots__ = ("decls",)

    def __init__(self, decls: list, loc: Loc):
        self.decls, self.loc = decls, loc


class Compound(Stmt):
    __slots__ = ("items", "end_loc")

    def __init__(self, items: list[Stmt], loc: Loc, end_loc: Loc):
        self.items, self.loc, self.end_loc = items, loc, end_loc


class ExprStmt(Stmt):
    __slots__ = ("expr",)

    def __init__(self, expr: Expr, loc: Loc):
        self.expr, self.loc = expr, loc


class If(Stmt):
    __slots__ = ("cond", "then", "other")

    def __init__(self, cond: Expr, then: Stmt, other: Stmt | None, loc: Loc):
        self.cond, self.then, self.other, self.loc = cond, then, other, loc


class While(Stmt):
    __slots__ = ("cond", "body")

    def __init__(self, cond: Expr, body: Stmt, loc: Loc):
        self.cond, self.body, self.loc = cond, body, loc


class DoWhile(Stmt):
    __slots__ = ("body", "cond", "while_loc")

    def __init__(self, body: Stmt, cond: Expr, loc: Loc, while_loc: Loc):
        self.body, self.cond, self.loc, self.while_loc = body, cond, loc, while_loc


class For(Stmt):
    __slots__ = ("init", "cond", "step", "body")

    def __init__(self, init, cond, step, body, loc: Loc):
        self.init, self.cond, self.step, self.body, self.loc = init, cond, step, body, loc


class Switch(Stmt):
    # table (set by sema): dict value -> item index; default_index: int | None
    __slots__ = ("expr", "body", "table", "default_index")

    def __init__(self, expr: Expr, body: Stmt, loc: Loc):
        self.expr, self.body, self.loc = expr, body, loc
        self.table, self.default_index = {}, None


class Case(Stmt):
    __slots__ = ("expr", "stmt", "value")

    def __init__(self, expr: Expr | None, stmt: Stmt, loc: Loc):
        self.expr, self.stmt, self.loc, self.value = expr, stmt, loc, None  # expr None = default


class Break(Stmt):
    __slots__ = ()

    def __init__(self, loc: Loc):
        self.loc = loc


class Continue(Stmt):
    __slots__ = ()

    def __init__(self, loc: Loc):
        self.loc = loc


class Return(Stmt):
    __slots__ = ("expr",)

    def __init__(self, expr: Expr | None, loc: Loc):
        self.expr, self.loc = expr, loc


class Empty(Stmt):
    __slots__ = ()

    def __init__(self, loc: Loc):
        self.loc = loc


class Param:
    __slots__ = ("name", "ty", "loc", "sym")

    def __init__(self, name: str | None, ty: CType, loc: Loc):
        self.name, self.ty, self.loc, self.sym = name, ty, loc, None


class FuncDef(Node):
    __slots__ = ("name", "ty", "params", "body", "sym", "storage")

    def __init__(self, name: str, ty: CType, params: list[Param], body: Compound, storage: str | None, loc: Loc):
        self.name, self.ty, self.params, self.body, self.storage, self.loc = name, ty, params, body, storage, loc
        self.sym = None


class TranslationUnit(Node):
    __slots__ = ("items",)

    def __init__(self, items: list[Node]):
        self.items = items
        self.loc = Loc(1, 1)


# --------------------------------------------------------------- pretty print

_PREC = {
    ",": 1, "=": 2, "?": 3, "||": 4, "&&": 5, "|": 6, "^": 7, "&": 8, "==": 9, "!=": 9,
    "<": 10, ">": 10, "<=": 10, ">=": 10, "<<": 11, ">>": 11, "+": 12, "-": 12, "*": 13, "/": 13, "%": 13,
}


def expr_str(e: Expr, prec: int = 0) -> str:
    """Render an expression back to C source (used in explanations)."""
    if isinstance(e, (Cast,)) and e.implicit:
        return expr_str(e.expr, prec)
    if isinstance(e, Decay):
        return expr_str(e.expr, prec)
    if isinstance(e, IntLit):
        return e.text or str(e.value)
    if isinstance(e, FloatLit):
        return e.text or repr(e.value)
    if isinstance(e, StrLit):
        s = e.data.decode("utf-8", "replace").encode("unicode_escape").decode().replace('"', '\\"')
        if len(s) > 24:
            s = s[:21] + "..."
        return f'"{s}"'
    if isinstance(e, Ident):
        return e.name
    if isinstance(e, Unary):
        inner = expr_str(e.operand, 14)
        r = f"{e.op}{inner}"
        return f"({r})" if prec > 14 else r
    if isinstance(e, Postfix):
        return f"{expr_str(e.operand, 15)}{e.op}"
    if isinstance(e, Binary):
        p = _PREC[e.op]
        r = f"{expr_str(e.left, p)} {e.op} {expr_str(e.right, p + 1)}"
        return f"({r})" if prec > p else r
    if isinstance(e, Assign):
        r = f"{expr_str(e.target, 3)} {e.op} {expr_str(e.value, 2)}"
        return f"({r})" if prec > 2 else r
    if isinstance(e, Cond):
        r = f"{expr_str(e.cond, 4)} ? {expr_str(e.then)} : {expr_str(e.other, 3)}"
        return f"({r})" if prec > 3 else r
    if isinstance(e, Comma):
        r = f"{expr_str(e.left, 1)}, {expr_str(e.right, 2)}"
        return f"({r})" if prec > 1 else r
    if isinstance(e, Call):
        args = ", ".join(expr_str(a, 2) for a in e.args)
        return f"{expr_str(e.func, 15)}({args})"
    if isinstance(e, Index):
        return f"{expr_str(e.base, 15)}[{expr_str(e.index)}]"
    if isinstance(e, Member):
        return f"{expr_str(e.obj, 15)}{'->' if e.arrow else '.'}{e.name}"
    if isinstance(e, Cast):
        from .ctype import PointerType, VoidType, type_str

        if isinstance(e.expr, IntLit) and e.expr.value == 0 and isinstance(e.ty, PointerType) and isinstance(e.ty.target, VoidType):
            return "NULL"
        r = f"({type_str(e.ty)}){expr_str(e.expr, 14)}"
        return f"({r})" if prec > 14 else r
    if isinstance(e, SizeofType):
        from .ctype import type_str

        return f"sizeof({type_str(e.target)})"
    if isinstance(e, SizeofExpr):
        return f"sizeof({expr_str(e.expr)})"
    if isinstance(e, InitList):
        return "{...}"
    return "?"

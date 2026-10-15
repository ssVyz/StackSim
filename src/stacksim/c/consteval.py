"""Evaluation of integer constant expressions (array sizes, enum values, case labels)."""

from __future__ import annotations

from . import ast as A
from .ctype import IntType


class NotConstant(Exception):
    pass


def eval_const(e: A.Expr, enum_value=None) -> int:
    """Evaluate ``e`` as an integer constant expression.

    ``enum_value(name)`` returns the value of an enum constant or None.
    Raises NotConstant if the expression is not a compile-time constant.
    """
    if isinstance(e, A.IntLit):
        return e.value
    if isinstance(e, A.Ident):
        if enum_value is not None:
            v = enum_value(e.name)
            if v is not None:
                return v
        raise NotConstant(e.name)
    if isinstance(e, A.Cast):
        v = eval_const(e.expr, enum_value)
        if isinstance(e.ty, IntType):
            return e.ty.wrap(v)
        raise NotConstant("cast")
    if isinstance(e, A.SizeofType):
        if not e.target.complete:
            raise NotConstant("sizeof incomplete")
        return e.target.size
    if isinstance(e, A.Unary):
        v = eval_const(e.operand, enum_value)
        if e.op == "-":
            return -v
        if e.op == "+":
            return v
        if e.op == "!":
            return int(not v)
        if e.op == "~":
            return ~v
        raise NotConstant(e.op)
    if isinstance(e, A.Binary):
        a = eval_const(e.left, enum_value)
        if e.op == "&&":
            return int(bool(a) and bool(eval_const(e.right, enum_value)))
        if e.op == "||":
            return int(bool(a) or bool(eval_const(e.right, enum_value)))
        b = eval_const(e.right, enum_value)
        op = e.op
        if op in ("/", "%"):
            if b == 0:
                raise NotConstant("division by zero")
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                q = -q
            return q if op == "/" else a - b * q
        ops = {
            "+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
            "<<": lambda: a << b, ">>": lambda: a >> b, "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b,
            "==": lambda: int(a == b), "!=": lambda: int(a != b), "<": lambda: int(a < b),
            ">": lambda: int(a > b), "<=": lambda: int(a <= b), ">=": lambda: int(a >= b),
        }
        if op in ops:
            return ops[op]()
        raise NotConstant(op)
    if isinstance(e, A.Cond):
        c = eval_const(e.cond, enum_value)
        return eval_const(e.then if c else e.other, enum_value)
    raise NotConstant(type(e).__name__)

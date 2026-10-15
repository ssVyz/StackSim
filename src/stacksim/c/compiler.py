"""Front-end driver: source text -> checked Program."""

from __future__ import annotations

from .errors import CompileError, Diagnostic
from .lexer import tokenize
from .parser import Parser
from .preprocessor import Preprocessor
from .sema import Program, Sema


class CompileResult:
    def __init__(self, program: Program | None, diagnostics: list[Diagnostic]):
        self.program = program
        self.diagnostics = diagnostics

    @property
    def ok(self) -> bool:
        return self.program is not None

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]


def compile_source(src: str) -> CompileResult:
    pre_diags: list[Diagnostic] = []
    try:
        toks = tokenize(src)
        pp = Preprocessor()
        toks = pp.run(toks)
        pre_diags = pp.diagnostics
        tu = Parser(toks).parse()
        prog = Sema(pp.included).check(tu)
    except CompileError as e:
        return CompileResult(None, pre_diags + [e.diagnostic])
    except RecursionError:
        return CompileResult(None, pre_diags + [Diagnostic("error", "the program is nested too deeply to be analysed", None)])
    prog.diagnostics = pre_diags + prog.diagnostics
    return CompileResult(prog, prog.diagnostics)

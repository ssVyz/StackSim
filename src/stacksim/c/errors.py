"""Source locations, diagnostics and the exceptions used by the front end."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Loc:
    """A position in the user's source file (1-based line and column)."""

    line: int
    col: int
    length: int = 1
    header: str | None = None  # set for tokens that come from a built-in header

    def __str__(self) -> str:
        return f"line {self.line}, column {self.col}"


NO_LOC = Loc(0, 0, 0)


@dataclass(slots=True)
class Diagnostic:
    """An error or warning shown to the learner.

    ``step`` is set for problems found while running (the trace step at which the
    problem happened); compile-time problems have ``step`` None.
    ``addrs`` lists memory ranges (start, size) that the GUI highlights.
    """

    severity: str  # "error" | "warning" | "note"
    message: str
    loc: Loc | None = None
    hint: str | None = None
    step: int | None = None
    kind: str = ""
    addrs: list[tuple[int, int]] = field(default_factory=list)

    @property
    def line(self) -> int | None:
        if self.loc is None or self.loc.line <= 0:
            return None
        return self.loc.line

    def format(self) -> str:
        where = f"Line {self.line}: " if self.line else ""
        text = f"{where}{self.severity}: {self.message}"
        if self.hint:
            text += f"\n    hint: {self.hint}"
        return text


class CompileError(Exception):
    """A problem that prevents the program from running at all."""

    def __init__(self, message: str, loc: Loc | None = None, hint: str | None = None, kind: str = "syntax"):
        super().__init__(message)
        self.diagnostic = Diagnostic("error", message, loc, hint, kind=kind)

    @property
    def loc(self) -> Loc | None:
        return self.diagnostic.loc

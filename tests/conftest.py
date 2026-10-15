"""Shared helpers for the stacksim test suite."""

from __future__ import annotations

import pytest

from stacksim.runner import output_text, run_source


def compile_errors(src: str):
    cr, tr = run_source(src)
    assert tr is None, "expected a compile error"
    return cr.errors


def run_ok(src: str, stdin: str = "", max_steps: int | None = None, max_frames: int | None = None):
    kwargs = {} if max_steps is None else {"max_steps": max_steps}
    if max_frames is not None:
        kwargs["max_frames"] = max_frames
    cr, tr = run_source(src, stdin, **kwargs)
    assert tr is not None, "compile failed: " + "; ".join(d.format() for d in cr.diagnostics)
    return cr, tr


def out(src: str, stdin: str = "") -> str:
    _, tr = run_ok(src, stdin)
    return output_text(tr, streams=("stdout",))


def kinds(tr) -> set[str]:
    return {d.kind for d in tr.diagnostics if d.severity in ("warning", "error")}


def main(body: str, headers=("stdio.h", "stdlib.h", "string.h")) -> str:
    inc = "".join(f"#include <{h}>\n" for h in headers)
    return f"{inc}int main(void) {{\n{body}\n}}\n"


@pytest.fixture
def helpers():
    return {"run_ok": run_ok, "out": out, "kinds": kinds, "main": main, "compile_errors": compile_errors}

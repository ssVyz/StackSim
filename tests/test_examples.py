"""Every bundled example compiles, runs, and shows the problem it is about."""

from __future__ import annotations

import pytest

from conftest import kinds
from stacksim.gui.examples import EXAMPLES
from stacksim.runner import output_text, run_source

EXPECTED = {
    "Arrays and out-of-bounds writes": {"out-of-bounds"},
    "Strings are char arrays": {"buffer-overflow"},
    "Memory leak": {"memory-leak"},
    "Use after free and double free": {"use-after-free", "double-free"},
    "Returning a pointer to a local variable": {"dangling-pointer"},
    "Uninitialized variables": {"uninitialized"},
    "Stack overflow (endless recursion)": {"stack-overflow"},
    "NULL pointer dereference": {"null-pointer"},
}

FATAL = {"Stack overflow (endless recursion)", "NULL pointer dereference"}


@pytest.mark.parametrize("ex", EXAMPLES, ids=[e.title for e in EXAMPLES])
def test_example_runs(ex):
    cr, tr = run_source(ex.code, ex.stdin)
    assert tr is not None, "; ".join(d.format() for d in cr.diagnostics)
    expected = EXPECTED.get(ex.title, set())
    found = kinds(tr)
    assert expected <= found, f"missing {expected - found}"
    if not expected:
        assert found == set(), found
    if ex.title in FATAL:
        assert tr.end_reason == "error"
    else:
        assert tr.end_reason == "exit"


def test_examples_have_unique_titles():
    titles = [e.title for e in EXAMPLES]
    assert len(titles) == len(set(titles))


def test_scanf_example_output():
    ex = next(e for e in EXAMPLES if e.title == "Reading input with scanf")
    assert ex.stdin == "", "the example starts without saved input, so the first run asks for it"
    answers = [b"3\n", b"10 20 12\n"]
    _, tr = run_source(ex.code, ex.stdin, input_provider=lambda req: answers.pop(0) if answers else None)
    assert output_text(tr).endswith("sum = 42\n")
    _, tr = run_source(ex.code, ex.stdin)  # without anyone typing: EOF, count stays 0
    assert output_text(tr).endswith("sum = 0\n")

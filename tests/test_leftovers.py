"""Leftover stack values: memory of variables that no longer exist, until it is reused."""

from __future__ import annotations

from conftest import run_ok

from stacksim.viewmodel import ViewBuilder

LOOP = """\
int main(void) {
    int total = 0;
    for (int i = 1; i <= 3; i++) {
        int square = i * i;
        total += square;
    }
    int y;
    return total + y;
}
"""

DANGLING = """\
int *make(void) {
    int n = 42;
    return &n;
}
int other(void) {
    int junk = 1000;
    return junk;
}
int main(void) {
    int *p = make();
    *p = 7;
    other();
    return 0;
}
"""


def first_step(tr, line, kind=None):
    return next(i for i, s in enumerate(tr.steps) if s.line == line and (kind is None or s.kind == kind))


def ghost_rows(state):
    return {v.name: v for g in state.ghost_frames for v in g.vars}


def test_leftovers_are_off_unless_requested():
    _, tr = run_ok(LOOP)
    vb = ViewBuilder(tr)
    s = vb.build(first_step(tr, 7))
    assert s.ghost_frames == [] and s.ghost_notes == [] and not s.show_ghosts


def test_loop_variables_stay_as_leftovers_after_the_loop():
    _, tr = run_ok(LOOP)
    vb = ViewBuilder(tr)
    s = vb.build(first_step(tr, 7), show_ghosts=True)  # the loop has just ended
    live = [v.name for v in s.frames[0].vars]
    assert live == ["total"]
    (g,) = s.ghost_frames
    assert g.ghost and g.ghost_scope and g.func == "main"
    rows = ghost_rows(s)
    assert rows["i"].value.text == "4" and rows["i"].ghost and rows["i"].ghost_reason == "out of scope"
    assert rows["square"].value.text == "9"
    assert all(v.addr < s.sp for v in rows.values()), "leftovers lie below the stack pointer"
    assert any("out of scope now" in n for n in s.ghost_notes)


def test_new_variable_takes_over_a_leftover():
    _, tr = run_ok(LOOP)
    vb = ViewBuilder(tr)
    s = vb.build(first_step(tr, 8), show_ghosts=True)  # after `int y;`
    rows = ghost_rows(s)
    assert "i" not in rows, "y now occupies i's old bytes"
    assert "square" in rows
    y = next(v for v in s.frames[0].vars if v.name == "y")
    assert y.value.text == "4" and not y.value.init, "y shows i's leftover value as garbage"
    assert any("y is placed in the memory where i (of main()) used to be" in n and "no value yet" in n for n in s.ghost_notes)


def test_returned_function_leaves_a_ghost_frame_and_a_dangling_pointer_lands_on_it():
    _, tr = run_ok(DANGLING)
    vb = ViewBuilder(tr)
    s = vb.build(first_step(tr, 11), show_ghosts=True)  # make() has returned
    (g,) = s.ghost_frames
    assert g.func == "make" and g.ghost and not g.ghost_scope
    assert ghost_rows(s)["n"].value.text == "42"
    assert {sl.kind for sl in g.slots} == {"retaddr", "savedfp"}
    p = s.frames[0].vars[0].value
    assert p.target == ghost_rows(s)["n"].addr and p.target_status == "dangling"
    assert any("make() has returned" in n for n in s.ghost_notes)


def test_writes_through_a_dangling_pointer_change_the_leftover():
    _, tr = run_ok(DANGLING)
    vb = ViewBuilder(tr)
    s = vb.build(first_step(tr, 12), show_ghosts=True)  # after *p = 7
    n = ghost_rows(s)["n"]
    assert n.value.text == "7" and n.value.changed


def test_new_frame_reuses_the_memory_and_the_leftover_disappears():
    _, tr = run_ok(DANGLING)
    vb = ViewBuilder(tr)
    inside = first_step(tr, 7)  # in other(), after `int junk = 1000;`
    s = vb.build(inside, show_ghosts=True)
    assert "n" not in ghost_rows(s)
    assert any("junk is placed in the memory where n (of make()) used to be." in n for n in s.ghost_notes)
    # once other() has returned, its own frame is the leftover now
    s = vb.build(first_step(tr, 13), show_ghosts=True)
    assert ghost_rows(s)["junk"].value.text == "1000"
    assert [g.func for g in s.ghost_frames] == ["other"]


def test_recursion_leaves_one_ghost_frame_per_returned_call():
    src = "int f(int n) {\n    if (n == 0) return 0;\n    return 1 + f(n - 1);\n}\nint main(void) {\n    int r = f(3);\n    return r;\n}\n"
    _, tr = run_ok(src)
    vb = ViewBuilder(tr)
    s = vb.build(first_step(tr, 7), show_ghosts=True)
    funcs = [g.func for g in s.ghost_frames]
    assert funcs == ["f", "f", "f", "f"]
    values = [g.vars[0].value.text for g in s.ghost_frames]
    assert values == ["3", "2", "1", "0"], "ordered from the highest address (oldest call) downwards"


def test_takeover_sweep_matches_a_brute_force_check():
    _, tr = run_ok(DANGLING)
    vb = ViewBuilder(tr)
    taken = vb.takeovers()
    objs = vb.stack_objs
    for o in objs:
        newer = [p for p in objs if p.id > o.id and p.addr < o.addr + max(o.size, 1) and o.addr < p.addr + max(p.size, 1)]
        if newer:
            assert taken[o.id] == (newer[0].birth, newer[0].id)
        else:
            assert o.id not in taken

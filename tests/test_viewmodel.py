"""The GUI-independent view model built for each step."""

from __future__ import annotations

from conftest import main, run_ok
from stacksim.viewmodel import ArrayView, Cell, StructView, ViewBuilder


def build_all(src: str, stdin: str = ""):
    _, tr = run_ok(src, stdin)
    vb = ViewBuilder(tr)
    return tr, vb


def step_after_line(tr, line: int) -> int:
    """The first step at which ``line`` has just been executed."""
    for i in range(1, len(tr.steps)):
        if tr.steps[i - 1].line == line:
            return i
    raise AssertionError(f"line {line} never ran")


def var(state, name):
    for f in state.frames:
        for v in f.vars:
            if v.name == name:
                return v
    for v in state.statics:
        if v.name == name:
            return v
    raise AssertionError(f"no variable {name}")


def test_leaked_block_status():
    src = main("int *p = malloc(16);\np = NULL;\nreturn 0;")
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 5))
    assert [b.status for b in s.blocks] == ["live"]
    s = vb.build(step_after_line(tr, 6))
    assert [b.status for b in s.blocks] == ["leaked"]
    assert s.leaked_bytes == 16


def test_block_reachable_through_other_block_is_not_leaked():
    src = """#include <stdlib.h>
struct n { int v; struct n *next; };
int main(void) {
    struct n *a = malloc(sizeof(struct n));
    a->next = malloc(sizeof(struct n));
    a->next->next = NULL;
    free(a->next);
    free(a);
    return 0;
}
"""
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 6))
    assert sorted(b.status for b in s.blocks) == ["live", "live"]


def test_freed_block_and_dangling_pointer():
    src = main("int *p = malloc(sizeof(int));\n*p = 3;\nfree(p);\nreturn 0;")
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 7))
    assert [b.status for b in s.blocks] == ["freed"]
    cell = var(s, "p").value
    assert isinstance(cell, Cell)
    assert cell.target_status == "dangling"
    s2 = vb.build(step_after_line(tr, 7), show_freed=False)
    assert s2.blocks == []


def test_pointer_statuses_null_uninit_ok():
    src = main("int x = 1;\nint *a;\nint *b = NULL;\nint *c = &x;\nreturn 0;")
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 8))
    assert var(s, "a").value.target_status == "uninit"
    assert var(s, "a").value.init is False
    assert var(s, "b").value.target_status == "null"
    assert var(s, "b").value.kind == "null"
    c = var(s, "c").value
    assert c.target_status == "ok"
    assert c.target == var(s, "x").addr


def test_frames_order_and_return_text():
    src = "int sq(int v) {\n return v * v;\n}\nint main(void) {\n int r = sq(4);\n return r;\n}"
    tr, vb = build_all(src)
    ret = next(i for i, st in enumerate(tr.steps) if st.kind == "return" and tr.frames[st.frame].func == "sq")
    s = vb.build(ret)
    assert [f.func for f in s.frames] == ["main", "sq"]
    assert s.frames[-1].is_top and not s.frames[0].is_top
    assert s.frames[-1].ret_text == "16"
    assert s.frames[0].line == 5


def test_char_array_string_preview():
    src = main('char s[8] = "hi";\nreturn 0;')
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 5))
    v = var(s, "s").value
    assert isinstance(v, ArrayView)
    assert v.text == "hi"
    assert v.elems[0].text == "'h'"


def test_struct_and_array_views():
    src = "struct pt { int x; int y; };\nint main(void) {\n struct pt p = {1, 2};\n int a[3] = {4, 5, 6};\n return 0;\n}"
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 4))
    p = var(s, "p").value
    assert isinstance(p, StructView)
    assert [(n, f.text) for n, f in p.fields] == [("x", "1"), ("y", "2")]
    a = var(s, "a").value
    assert [e.text for e in a.elems] == ["4", "5", "6"]


def test_statics_show_globals_and_pointed_literal():
    src = '#include <stdio.h>\nint counter = 5;\nconst char *msg = "hello";\nint main(void) {\n return counter;\n}'
    tr, vb = build_all(src)
    s = vb.build(0)
    names = [v.name for v in s.statics]
    assert "counter" in names and "msg" in names
    assert any(v.name == "string literal" for v in s.statics)
    # format strings that nobody points to stay hidden
    assert sum(1 for v in s.statics if v.name == "string literal") == 1


def test_changed_cells_marked():
    src = main("int x = 1;\nx = 2;\nreturn 0;")
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 5))
    assert var(s, "x").value.changed
    assert var(s, "x").is_new


def test_heap_block_typed_by_cast():
    src = main("int *a = malloc(3 * sizeof(int));\na[0] = 7;\nfree(a);\nreturn 0;")
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 6))
    b = s.blocks[0]
    assert b.type_name == "int[3]"
    assert b.value.elems[0].text == "7"
    assert b.value.elems[1].init is False


def test_untyped_heap_block_shown_as_bytes():
    src = main("void *v = malloc(5);\nfree(v);\nreturn 0;")
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 5))
    assert s.blocks[0].type_name == "5 bytes"
    assert len(s.blocks[0].value.elems) == 5


def test_large_array_is_truncated():
    src = main("int big[200];\nfor (int i = 0; i < 200; i++) big[i] = i;\nreturn 0;")
    _, tr = run_ok(src, max_steps=5000)
    vb = ViewBuilder(tr)
    s = vb.build(len(tr.steps) - 2)
    a = var(s, "big").value
    assert a.total == 200
    assert a.hidden > 0
    assert a.elems[-1].label == "[199]"


def test_deep_recursion_frames_are_collapsed():
    src = "int f(int n) { return n == 0 ? 0 : f(n - 1); }\nint main(void) { return f(40); }"
    tr, vb = build_all(src)
    deepest = max(range(len(tr.steps)), key=lambda i: tr.frames[tr.steps[i].frame].depth if tr.steps[i].frame is not None else -1)
    s = vb.build(deepest)
    assert s.frames_hidden > 0
    assert s.frames[0].func == "main"
    assert len(s.frames) + s.frames_hidden == 42


def test_problem_ranges_at_warning_step():
    src = main("int a[2];\na[2] = 1;\nreturn 0;")
    tr, vb = build_all(src)
    d = next(d for d in tr.diagnostics if d.kind == "out-of-bounds")
    s = vb.build(d.step)
    assert s.diagnostics and s.problem_ranges


def test_function_pointer_cell():
    src = "int add(int a, int b) { return a + b; }\nint main(void) {\n int (*f)(int, int) = add;\n return f(1, 2);\n}"
    tr, vb = build_all(src)
    s = vb.build(step_after_line(tr, 3))
    c = var(s, "f").value
    assert c.kind == "fn"
    assert c.text == "add()"


def test_every_step_builds():
    src = main(
        """char *s = malloc(6);
strcpy(s, "hello");
char *t = realloc(s, 100);
int arr[3] = {1, 2, 3};
int *p = arr + 3;
free(t);
return 0;"""
    )
    tr, vb = build_all(src)
    for i in range(len(tr.steps)):
        vb.build(i)
    for i in range(len(tr.steps) - 1, -1, -1):
        vb.build(i)

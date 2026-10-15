"""Memory that the program reads in a step is marked, next to what it writes."""

from __future__ import annotations

from conftest import run_ok

from stacksim.viewmodel import ArrayView, Cell, StructView, ViewBuilder

PROGRAM = """\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct myobject {
    int length;
    int nums[5];
    int result;
} t_myobject;

t_myobject *mkobj(void) {
    t_myobject *template = malloc(sizeof(t_myobject));
    int arr[5] = {1, 2, 3, 4, 5};
    memcpy(template->nums, arr, sizeof(int) * 5);
    template->length = 5;
    return template;
}

void calc_result(t_myobject *s) {
    int res = 0;
    for (int i = 0; i < s->length; i++) {
        res += s->nums[i];
    }
    s->result = res;
}

int main(void) {
    t_myobject *app = mkobj();
    calc_result(app);
    printf("The result is: %d\\n", app->result);
    free(app);
    return 0;
}
"""


def after_line(tr, line, nth=0):
    """The step right after line ``line`` ran for the ``nth`` time."""
    hits = [i for i in range(1, len(tr.steps)) if tr.steps[i - 1].line == line]
    return hits[nth]


def field(block, name):
    return dict(block.value.fields)[name]


def var(frame, name):
    return next(v for v in frame.vars if v.name == name).value


def test_reads_are_recorded_per_step():
    _, tr = run_ok(PROGRAM)
    step = tr.steps[after_line(tr, 22)]  # res += s->nums[i];
    assert step.reads, "the step read memory"
    assert all(size > 0 for _, size in step.reads)


def test_loop_body_marks_read_heap_cells_and_written_variable():
    _, tr = run_ok(PROGRAM)
    vb = ViewBuilder(tr)
    s = vb.build(after_line(tr, 22, nth=1))  # second round: reads nums[1]
    frame = s.frames[-1]
    block = s.blocks[0]
    nums = field(block, "nums")
    assert isinstance(nums, ArrayView)
    assert nums.elems[1].read and not nums.elems[1].changed
    assert not nums.elems[0].read and not nums.elems[2].read, "only the element used in this step"
    assert var(frame, "s").read and var(frame, "i").read
    res = var(frame, "res")
    assert res.changed, "res was read and written; the write wins"
    assert not field(block, "length").read


def test_loop_condition_reads_the_length():
    _, tr = run_ok(PROGRAM)
    vb = ViewBuilder(tr)
    s = vb.build(after_line(tr, 21, nth=1))  # i++ and i < s->length
    block = s.blocks[0]
    assert field(block, "length").read
    assert var(s.frames[-1], "i").changed


def test_library_functions_mark_what_they_read():
    _, tr = run_ok(PROGRAM)
    vb = ViewBuilder(tr)
    s = vb.build(after_line(tr, 14))  # memcpy(template->nums, arr, ...)
    arr = var(s.frames[-1], "arr")
    assert all(c.read for c in arr.elems), "memcpy read the whole source array"
    nums = field(s.blocks[0], "nums")
    assert all(c.changed for c in nums.elems)


def test_returning_reads_the_frame_slots():
    _, tr = run_ok(PROGRAM)
    vb = ViewBuilder(tr)
    back = after_line(tr, 16, nth=0)  # step after `return template;` -> frame popped
    while tr.steps[back].kind == "return":
        back += 1
    s = vb.build(back, show_ghosts=True)
    ghost = next(g for g in s.ghost_frames if g.func == "mkobj")
    assert ghost.slots and all(sl.cell.read for sl in ghost.slots)


def test_nothing_is_marked_without_reads():
    _, tr = run_ok(PROGRAM)
    vb = ViewBuilder(tr)
    s = vb.build(0)

    def cells(v):
        if isinstance(v, Cell):
            yield v
        elif isinstance(v, ArrayView):
            for e in v.elems:
                yield from cells(e)
        elif isinstance(v, StructView):
            for _, f in v.fields:
                yield from cells(f)

    assert not any(c.read for f in s.frames for v in f.vars for c in cells(v.value))

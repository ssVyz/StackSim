"""Return addresses and saved frame pointers at the top of every stack frame."""

from __future__ import annotations

from conftest import run_ok

from stacksim.viewmodel import ViewBuilder

CALLS = """\
int square(int x) {
    return x * x;
}

int twice(int y) {
    int s = square(y);
    return s + s;
}

int main(void) {
    int n = twice(3);
    return n;
}
"""


def _frame(tr, name):
    return next(f for f in tr.frames if f.func == name)


def _slot_value(tr, oid):
    o = tr.objects[oid]
    data, _ = tr.memory.read(o.addr, 8)
    return o.addr, int.from_bytes(data, "little")


def test_slots_sit_at_the_top_of_each_frame_in_x86_64_order():
    _, tr = run_ok(CALLS)
    for fr in tr.frames:
        ret = tr.objects[fr.ret_obj]
        fp = tr.objects[fr.fp_obj]
        assert ret.addr == fp.addr + 8, "the return address lies directly above the saved frame pointer"
        assert ret.addr % 8 == 0 and fp.addr % 16 == 0, "the stack is 16-byte aligned at a call"
        for oid in fr.vars:
            assert tr.objects[oid].addr < fp.addr, "variables lie below the bookkeeping slots"


def test_return_addresses_lead_back_to_the_call_site():
    _, tr = run_ok(CALLS)
    twice, square, main = _frame(tr, "twice"), _frame(tr, "square"), _frame(tr, "main")
    assert tr.describe_code(twice.ret_addr) == "main(), line 11"
    assert tr.describe_code(square.ret_addr) == "twice(), line 6"
    assert tr.return_sites[square.ret_addr][1] == 6
    assert "C runtime" in tr.describe_code(main.ret_addr)
    # the code addresses lie inside the caller's code range
    start, end, name = next(r for r in tr.code_ranges if r[2] == "main")
    assert start <= twice.ret_addr < end


def test_saved_frame_pointers_form_a_chain():
    _, tr = run_ok(CALLS)
    main, twice, square = _frame(tr, "main"), _frame(tr, "twice"), _frame(tr, "square")
    assert main.saved_fp == 0
    assert twice.saved_fp == tr.objects[main.fp_obj].addr
    assert square.saved_fp == tr.objects[twice.fp_obj].addr


def test_slot_bytes_in_memory_match_the_recorded_values():
    _, tr = run_ok(CALLS)
    vb = ViewBuilder(tr)
    call_step = next(i for i, s in enumerate(tr.steps) if s.kind == "call" and tr.frames[s.frame].func == "square")
    vb.build(call_step)
    sq = tr.frames[tr.steps[call_step].frame]
    assert _slot_value(tr, sq.ret_obj)[1] == sq.ret_addr
    assert _slot_value(tr, sq.fp_obj)[1] == sq.saved_fp


def test_view_model_slots_and_notes():
    _, tr = run_ok(CALLS)
    vb = ViewBuilder(tr)
    call_step = next(i for i, s in enumerate(tr.steps) if s.kind == "call" and tr.frames[s.frame].func == "square")
    state = vb.build(call_step)
    main_v, twice_v, square_v = state.frames
    kinds = [s.kind for s in square_v.slots]
    assert kinds == ["retaddr", "savedfp"]
    ret, fp = square_v.slots
    assert ret.description == "return to twice(), line 6"
    assert ret.line == 6 and ret.intact
    assert ret.cell.changed, "the slot was written by this call"
    assert fp.cell.target == twice_v.slots[1].cell.addr
    assert fp.cell.target_status == "frame"
    assert main_v.slots[1].description.startswith("0:")
    assert any("pushed the return address" in n for n in state.frame_notes)
    # after square returns, the notes explain the return
    back = next(i for i in range(call_step, len(tr.steps)) if tr.steps[i].frame is not None and tr.frames[tr.steps[i].frame].func == "twice")
    notes = vb.build(back).frame_notes
    assert any("square() returned" in n and "twice(), line 6" in n for n in notes)


SMASH = """\
#include <string.h>
void f(void) {
    char buf[4];
    strcpy(buf, "AAAAAAAABBBBBBBBCCCC");
}
int main(void) {
    f();
    return 0;
}
"""


def test_overwritten_return_address_warns_and_continues():
    _, tr = run_ok(SMASH)
    assert tr.end_reason == "exit"
    msgs = [d.message for d in tr.diagnostics if d.kind == "stack-smashing"]
    assert any("return address in f()'s frame was overwritten" in m and "main(), line 7" in m for m in msgs)
    assert any("saved frame pointer in f()'s frame was overwritten" in m for m in msgs)


def test_view_marks_overwritten_slots():
    _, tr = run_ok(SMASH)
    vb = ViewBuilder(tr)
    # the step after strcpy, still inside f()
    step = next(i for i, s in enumerate(tr.steps) if s.line == 5 and tr.frames[s.frame].func == "f")
    f_view = vb.build(step).frames[-1]
    assert not f_view.slots[0].intact and f_view.slots[0].description.startswith("OVERWRITTEN")
    assert not f_view.slots[1].intact
    assert f_view.header_problem


def test_out_of_bounds_message_names_the_slot():
    src = "void f(void) {\n    int a[2];\n    a[2] = 1;\n    a[4] = 2;\n}\nint main(void) { f(); return 0; }\n"
    _, tr = run_ok(src)
    msgs = [d.message for d in tr.diagnostics if d.kind == "out-of-bounds"]
    assert any("saved frame pointer in f()'s stack frame" in m for m in msgs)
    assert any("return address stored in f()'s stack frame" in m for m in msgs)


RECURSE = "int f(int n) { return n == 0 ? 0 : 1 + f(n - 1); }\nint main(void) { return f(%d); }\n"


def test_default_frame_limit_is_15_frames():
    _, tr = run_ok(RECURSE % 13)  # main + 14 calls of f = 15 frames
    assert tr.end_reason == "exit" and tr.exit_code == 13
    _, tr = run_ok(RECURSE % 14)  # would need 16 frames
    assert tr.end_reason == "error"
    assert tr.fatal.kind == "stack-overflow"
    assert "more than 15 stack frames" in tr.fatal.message
    assert "Frame limit" in tr.fatal.hint
    assert max(fr.depth for fr in tr.frames) == 14


def test_frame_limit_is_configurable():
    _, tr = run_ok(RECURSE % 40, max_frames=50)
    assert tr.end_reason == "exit" and tr.exit_code == 40
    _, tr = run_ok(RECURSE % 40, max_frames=10)
    assert tr.fatal.kind == "stack-overflow" and "more than 10" in tr.fatal.message


def test_big_local_arrays_still_overflow_the_stack_bytes():
    src = "int f(void) { char big[100000]; big[0] = 1; return big[0]; }\nint g(void) { return f() + f(); }\nint main(void) { char more[60000]; more[0] = 0; return g(); }\n"
    _, tr = run_ok(src)
    assert tr.fatal.kind == "stack-overflow"
    assert "stack is full" in tr.fatal.message


def test_qsort_callback_returns_into_the_library():
    src = """\
#include <stdlib.h>
int cmp(const void *a, const void *b) { return *(const int *)a - *(const int *)b; }
int main(void) {
    int v[3] = {3, 1, 2};
    qsort(v, 3, sizeof(int), cmp);
    return 0;
}
"""
    _, tr = run_ok(src)
    fr = _frame(tr, "cmp")
    assert "qsort() in the C library" in tr.describe_code(fr.ret_addr)

"""Command-line arguments: argc/argv set up on the stack before main() starts."""

from __future__ import annotations

from stacksim.runner import output_text, run_source
from stacksim.viewmodel import ViewBuilder

ECHO = """\
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char *argv[]) {
    printf("argc=%d\\n", argc);
    for (int i = 0; i < argc; i++) {
        printf("[%s]\\n", argv[i]);
    }
    printf("null=%d\\n", argv[argc] == NULL);
    return argc > 1 ? atoi(argv[1]) : 0;
}
"""


def run(args=None, prog_name="./prog", src=ECHO):
    cr, tr = run_source(src, args=args, prog_name=prog_name)
    assert tr is not None, [d.format() for d in cr.diagnostics]
    return tr


def test_arguments_reach_main():
    tr = run(["7", "two words", ""], "./echo")
    assert output_text(tr) == "argc=4\n[./echo]\n[7]\n[two words]\n[]\nnull=1\n"
    assert tr.exit_code == 7
    assert tr.args == ["./echo", "7", "two words", ""]


def test_without_arguments_argc_is_one():
    tr = run()
    assert output_text(tr) == "argc=1\n[./prog]\nnull=1\n"


def test_arguments_live_at_the_top_of_the_stack_above_main():
    tr = run(["abc"])
    args = [o for o in tr.objects if o.kind == "arg"]
    names = sorted(o.name for o in args)
    assert names == ["argv array", "argv[0] text", "argv[1] text"]
    main_fp = tr.objects[tr.frames[0].fp_obj].addr
    assert all(o.region is not None and o.region.kind == "stack" for o in args)
    assert all(o.addr > main_fp for o in args), "above main()'s frame"
    array = next(o for o in args if o.name == "argv array")
    assert array.size == 8 * 3, "two pointers and the final NULL"
    assert array.addr % 16 == 0


def test_view_shows_the_arguments_card_with_arrows():
    tr = run(["abc"])
    vb = ViewBuilder(tr)
    s = vb.build(1)
    card = s.args_frame
    assert card is not None and card.title.startswith("Command-line arguments (argc = 2)")
    rows = {v.name: v for v in card.vars}
    assert rows["argv[1] text"].value.text == "abc"
    array = rows["argv array"].value
    assert array.elems[1].target == rows["argv[1] text"].addr and array.elems[1].target_status == "ok"
    assert array.elems[2].text == "NULL"
    argv_param = next(v for v in s.frames[0].vars if v.name == "argv").value
    assert argv_param.target == rows["argv array"].addr


def test_no_arguments_card_for_main_without_parameters():
    tr = run(src="int main(void) { return 0; }")
    s = ViewBuilder(tr).build(0)
    assert s.args_frame is None


def test_writing_past_an_argument_is_detected():
    src = """\
#include <string.h>
int main(int argc, char *argv[]) {
    strcpy(argv[1], "much longer than before");
    return 0;
}
"""
    tr = run(["ab"], src=src)
    msgs = [d.message for d in tr.diagnostics if d.kind == "buffer-overflow"]
    assert any("argv[1] text" in m for m in msgs)

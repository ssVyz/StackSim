"""Interactive input: the VM asks for more input when the prepared stdin is used up."""

from __future__ import annotations

from stacksim.runner import output_text, run_source
from stacksim.vm.interpreter import Cancelled

SUM = """\
#include <stdio.h>
int main(void) {
    int n = 0, x = 0, sum = 0;
    printf("How many? ");
    scanf("%d", &n);
    for (int i = 0; i < n; i++) {
        scanf("%d", &x);
        sum += x;
    }
    printf("sum=%d\\n", sum);
    return 0;
}
"""


class Typist:
    """A fake user: answers input requests from a list (None = end of input)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        if not self.answers:
            return None
        a = self.answers.pop(0)
        if a == "STOP":
            raise Cancelled()
        return None if a is None else a.encode()


def test_scanf_asks_when_the_input_is_used_up():
    user = Typist("3\n", "10 20\n", "12\n")
    _, tr = run_source(SUM, "", input_provider=user)
    assert output_text(tr) == "How many? sum=42\n"
    assert len(user.requests) == 3, "one line can serve several scanf calls"
    first = user.requests[0]
    assert first["line"] == 5 and first["func"] == "scanf"
    assert first["call"] == 'scanf("%d", &n)' and first["format"] == "%d"
    assert first["output"] == "How many? "
    assert tr.stdin == b"3\n10 20\n12\n", "the trace keeps everything that was typed"
    events = [e for s in tr.steps for e in s.events]
    assert 'You typed "10 20" as input for scanf().' in events


def test_prepared_input_is_used_before_asking():
    user = Typist("12\n")
    _, tr = run_source(SUM, "3\n10 20\n", input_provider=user)
    assert output_text(tr) == "How many? sum=42\n"
    assert len(user.requests) == 1 and user.requests[0]["line"] == 7


def test_no_questions_when_everything_is_prepared_or_eof_is_set():
    user = Typist()
    _, tr = run_source(SUM, "2\n1 2\n", input_provider=user)
    assert output_text(tr) == "How many? sum=3\n" and user.requests == []
    _, tr = run_source(SUM, "2\n1\n", input_provider=user, stdin_eof=True)
    assert user.requests == [], "with EOF set, the program gets end of input instead of a question"
    assert tr.end_reason == "exit"


def test_end_of_input_answer():
    user = Typist("2\n", None)
    _, tr = run_source(SUM, "", input_provider=user)
    assert len(user.requests) == 2
    events = [e for s in tr.steps for e in s.events]
    assert any("input was ended (EOF)" in e for e in events)
    # after EOF the program is not asked again, even though it tries to read once more
    assert tr.end_reason == "exit"


def test_stop_answer_cancels_the_run():
    user = Typist("STOP")
    _, tr = run_source(SUM, "", input_provider=user)
    assert tr.end_reason == "cancelled"


def test_getchar_and_fgets_ask_too():
    src = """\
#include <stdio.h>
int main(void) {
    char line[20];
    int c = getchar();
    fgets(line, 20, stdin);
    fgets(line, 20, stdin);
    printf("%c|%s", c, line);
    return 0;
}
"""
    user = Typist("x\n", "hello\n")
    _, tr = run_source(src, "", input_provider=user)
    # Like in real C, getchar() leaves the newline behind, so the first fgets()
    # gets just "\n" without asking; only the second one asks.
    assert [r["func"] for r in user.requests] == ["getchar", "fgets"]
    assert user.requests[1]["line"] == 6
    assert output_text(tr) == "x|hello\n"


def test_without_a_provider_reading_gives_eof():
    _, tr = run_source(SUM, "")
    assert output_text(tr) == "How many? sum=0\n"

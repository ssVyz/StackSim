"""Offscreen smoke test of the main window (no real display needed)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir("C:/Windows/Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

import pytest

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path):
    from stacksim.gui.main_window import MainWindow

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings=settings)
    # Never open real (modal) dialogs in tests: answer "end of input", "keep the inputs" and "no arguments"
    # unless a test says otherwise.
    w.ask_input = lambda req: ("eof", "")
    w.ask_reuse_inputs = lambda summary: "keep"
    w.ask_args = lambda prog_name, current: []
    w.resize(1400, 900)
    w.show()
    qapp.processEvents()
    yield w
    w.pause()
    w.editor.document().setModified(False)
    w.file_path = None
    w.close()
    qapp.processEvents()


def run_and_wait(w, qapp, timeout_ms: int = 30000) -> None:
    loop = QEventLoop()
    w.bridge.finished.connect(lambda *a: QTimer.singleShot(0, loop.quit))
    QTimer.singleShot(timeout_ms, loop.quit)
    w.run()
    if w.running:
        loop.exec()
    qapp.processEvents()
    assert not w.running, "the run did not finish in time"


def load(w, title: str) -> None:
    from stacksim.gui.examples import EXAMPLES

    ex = next(e for e in EXAMPLES if e.title.startswith(title))
    w.load_example(ex)
    w.speed.setCurrentIndex(0)  # manual: do not auto-play


def test_run_and_navigate(window, qapp):
    w = window
    load(w, "Linked list")
    run_and_wait(w, qapp)
    assert w.trace is not None
    last = w.last_step()
    assert last > 10
    assert w.pos == 0
    assert w.canvas.layout_ is not None

    w.step_forward()
    assert w.pos == 1
    w.step_back()
    assert w.pos == 0

    w.goto(last // 2)
    assert w.pos == last // 2
    assert w.canvas.layout_.anchors
    assert w.canvas.layout_.ptrs, "the linked list should produce pointer arrows"
    before = w.pos
    w.step_over()
    assert w.pos > before
    w.step_back_over()
    assert w.pos <= before

    w.goto(last)
    assert "finished" in w.output.toPlainText()
    assert w.step_label.text() == f"Step {last + 1} / {last + 1}"
    w.goto(0)
    assert "finished" not in w.output.toPlainText()


def test_play_and_breakpoints(window, qapp):
    w = window
    load(w, "Function calls and stack frames")
    run_and_wait(w, qapp)
    line = next(st.line for st in w.trace.steps if st.kind == "stmt" and st.line and st.line > 4)
    w.editor.toggle_breakpoint(line)
    stop = w.find_stop(0, 1)
    assert w.trace.steps[stop].line == line
    assert w.find_stop(stop, -1) == 0

    w.speed.setCurrentText("Very fast (30 steps/s)")
    w.toggle_play(1)
    assert w.timer.isActive()
    w.toggle_play(1)
    assert not w.timer.isActive()

    w.speed.setCurrentIndex(len(__import__("stacksim.gui.main_window", fromlist=["SPEEDS"]).SPEEDS) - 1)  # instant
    w.goto(0)
    w.toggle_play(1)
    assert w.pos == stop
    w.clear_breakpoints()
    w.toggle_play(1)
    assert w.pos == w.last_step()


def test_compile_error_is_shown(window, qapp):
    w = window
    w.editor.setPlainText("int main(void) {\n    int x = 5\n    return x;\n}\n")
    w.run()
    qapp.processEvents()
    assert w.trace is None
    assert w.problems.topLevelItemCount() >= 1
    assert "';'" in w.problems.topLevelItem(0).text(3)
    assert 2 in w.editor.diags


def test_editing_marks_run_stale(window, qapp):
    w = window
    load(w, "Variables live on the stack")
    run_and_wait(w, qapp)
    assert w.can_play()
    w.editor.insertPlainText(" ")
    assert w.stale
    assert not w.can_play()
    run_and_wait(w, qapp)
    assert not w.stale


def test_view_options_and_zoom(window, qapp):
    w = window
    load(w, "Strings are char arrays")
    run_and_wait(w, qapp)
    w.goto(w.last_step() - 2)
    w.act_bytes.setChecked(True)
    w.set_canvas_option("show_bytes", True)
    w.set_canvas_option("show_addresses", False)
    w.set_canvas_option("show_freed", False)
    w.canvas.set_zoom(1.5)
    img = w.grab()
    assert not img.isNull()
    w.canvas.set_zoom(1.0)


def test_every_example_renders(window, qapp):
    from stacksim.gui.examples import EXAMPLES

    w = window
    for ex in EXAMPLES:
        w.load_example(ex)
        w.speed.setCurrentIndex(0)
        run_and_wait(w, qapp)
        assert w.trace is not None, ex.title
        for s in (0, w.last_step() // 2, w.last_step()):
            w.goto(s)
            w.canvas.grab()


def test_stdin_tab_and_problem_click(window, qapp):
    w = window
    load(w, "Use after free")
    run_and_wait(w, qapp)
    items = [w.problems.topLevelItem(i) for i in range(w.problems.topLevelItemCount())]
    runtime = [it for it in items if it.text(2)]
    assert runtime
    w.on_problem_clicked(runtime[0])
    step, _ = runtime[0].data(0, 0x0100)
    assert w.pos == min(step, w.last_step())


def test_return_address_toggle(window, qapp, tmp_path):
    w = window
    load(w, "Function calls")
    run_and_wait(w, qapp)
    call = next(i for i, s in enumerate(w.trace.steps) if s.kind == "call" and w.trace.frames[s.frame].func == "square")
    w.goto(call)
    assert not w.act_frames.isChecked(), "hidden by default"
    assert not w.canvas.layout_.line_links
    assert "pushed the return address" not in w.explain.toPlainText()

    w.act_frames.setChecked(True)
    w.set_canvas_option("show_frame_info", True)
    lay = w.canvas.layout_
    assert lay.line_links, "return addresses link back to source lines"
    assert any(status == "frame" for *_, status in lay.ptrs), "saved frame pointers are drawn as arrows"
    assert "pushed the return address" in w.explain.toPlainText()

    # pointing at a return address highlights its line in the editor
    rect, line = lay.line_links[-1]
    w.canvas.lineHovered.emit(line)
    assert w.editor.hover_line == line
    w.canvas.lineHovered.emit(None)
    assert w.editor.hover_line is None

    # the choice is remembered
    w.save_settings()
    assert w.settings.value("show_frame_info") in (True, "true")


def test_leftover_values_toggle(window, qapp):
    w = window
    load(w, "Returning a pointer")
    run_and_wait(w, qapp)
    after_return = next(i for i, s in enumerate(w.trace.steps) if s.line == 15)
    w.goto(after_return)
    assert not w.act_ghosts.isChecked(), "hidden by default"
    assert w.state.ghost_frames == []

    w.act_ghosts.setChecked(True)
    w.set_canvas_option("show_ghosts", True)
    assert [g.func for g in w.state.ghost_frames] == ["make_number"]
    lay = w.canvas.layout_
    texts = [op[2] for op in lay.ops if op[0] == "text"]
    assert any("stack pointer" in t for t in texts)
    assert any("make_number() - has returned" in t for t in texts)
    assert "make_number() has returned" in w.explain.toPlainText()
    # the dangling pointer now has a drawn target: the leftover n
    n_addr = w.state.ghost_frames[0].vars[0].addr
    assert lay.resolve(n_addr, 4, "dangling")[0] is not None

    w.save_settings()
    assert w.settings.value("show_ghosts") in (True, "true")


def test_frame_limit_setting_is_used_for_runs(window, qapp):
    w = window
    load(w, "Recursion: factorial")  # factorial(4): main + 4 frames
    w.max_frames = 3
    run_and_wait(w, qapp)
    assert w.trace.end_reason == "error"
    assert "more than 3 stack frames" in w.trace.fatal.message
    w.max_frames = 15
    run_and_wait(w, qapp)
    assert w.trace.end_reason == "exit"
    w.save_settings()
    assert int(w.settings.value("max_frames")) == 15


def test_interactive_input_is_asked_saved_and_reset(window, qapp):
    w = window
    load(w, "Reading input with scanf")
    w.clear_input()
    asked = []
    answers = ["3", "10 20", "12"]

    def fake_ask(req):
        asked.append(req)
        return ("line", answers.pop(0)) if answers else ("eof", "")

    w.ask_input = fake_ask
    run_and_wait(w, qapp)
    assert w.trace.end_reason == "exit"
    assert len(asked) == 3 and asked[0]["output"] == "How many numbers? "
    assert w.stdin_edit.toPlainText() == "3\n10 20\n12\n", "answers are saved in the Input tab"
    w.goto(w.last_step())
    assert "sum = 42" in w.output.toPlainText().replace("\n", " ")

    # the next run uses the saved input without asking
    asked.clear()
    run_and_wait(w, qapp)
    assert asked == [] and w.trace.end_reason == "exit"

    # after clearing, the user is asked again
    w.clear_input()
    answers[:] = ["1", "5"]
    run_and_wait(w, qapp)
    assert len(asked) == 2
    assert w.stdin_edit.toPlainText() == "1\n5\n"


def test_input_dialog_eof_and_stop(window, qapp):
    w = window
    load(w, "Reading input with scanf")
    w.clear_input()
    w.ask_input = lambda req: ("eof", "")
    run_and_wait(w, qapp)
    assert w.stdin_eof.isChecked(), "EOF is remembered"
    assert w.trace is not None  # count stays uninitialized after a failed scanf, so the run may hit the step limit

    w.clear_input()
    assert not w.stdin_eof.isChecked()
    w.ask_input = lambda req: ("stop", "")
    run_and_wait(w, qapp)
    assert w.trace.end_reason == "cancelled"


def test_typed_input_is_joined_on_a_new_line(window, qapp):
    w = window
    load(w, "Reading input with scanf")
    w.stdin_edit.setPlainText("2\n4")  # no newline at the end
    w.stdin_eof.setChecked(False)
    w.ask_input = lambda req: ("line", "6")
    run_and_wait(w, qapp)
    assert w.stdin_edit.toPlainText() == "2\n4\n6\n"
    w.goto(w.last_step())
    assert "sum = 10" in w.output.toPlainText().replace("\n", " ")


def test_stop_pauses_playback(window, qapp):
    w = window
    load(w, "Linked list")
    w.speed.setCurrentText("Very slow (1 step/s)")
    run_and_wait(w, qapp)
    assert not w.timer.isActive(), "Execute only records; it does not start the playback"
    w.act_play.trigger()
    assert w.timer.isActive(), "playback started"
    assert w.act_stop.isEnabled(), "Stop is available during playback"
    assert w.act_play.text() == "Pause"
    pos = w.pos
    w.act_stop.trigger()
    assert not w.timer.isActive()
    assert w.pos == pos, "stopping keeps the current step"
    assert not w.act_stop.isEnabled()
    assert w.act_play.text() == "Play"
    # Esc is a shortcut for Stop
    assert "Esc" in [s.toString() for s in w.act_stop.shortcuts()]


ARGS_AND_INPUT = """\
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char *argv[]) {
    int times = argc > 1 ? atoi(argv[1]) : 1;
    int x = 0;
    scanf("%d", &x);
    for (int i = 0; i < times; i++) printf("%d\\n", x);
    return 0;
}
"""


def test_execute_records_and_play_is_separate(window, qapp):
    w = window
    load(w, "Function calls and stack frames")
    assert not w.act_play.isEnabled(), "nothing to play before the first execution"
    assert w.act_execute.isEnabled() and w.act_execute.text() == "Execute"
    run_and_wait(w, qapp)
    assert w.trace is not None and w.pos == 0
    assert not w.timer.isActive(), "Execute does not start the playback"
    assert w.act_play.isEnabled()
    w.editor.insertPlainText(" ")
    assert w.stale and not w.act_play.isEnabled(), "changing the code makes the run outdated"
    assert w.act_execute.isEnabled()
    w.editor.document().setModified(False)


def test_inputs_are_asked_on_execute_and_locked(window, qapp):
    w = window
    w.set_source(ARGS_AND_INPUT)
    asked_args, asked_input, asked_reuse = [], [], []
    w.ask_args = lambda name, cur: asked_args.append((name, cur)) or ["3"]
    w.ask_input = lambda req: asked_input.append(req) or ("line", "7")
    w.ask_reuse_inputs = lambda summary: asked_reuse.append(summary) or "keep"

    run_and_wait(w, qapp)
    assert asked_args == [("./prog", [])]
    assert len(asked_input) == 1 and asked_reuse == []
    assert w.trace.args == ["./prog", "3"]
    w.goto(w.last_step())
    assert w.output.toPlainText().startswith("7\n7\n7\n")
    # the inputs belong to the run now
    assert w.inputs.locked and w.stdin_edit.isReadOnly() and w.inputs.lock_bar.isVisibleTo(w.inputs)
    assert w.inputs.args() == ["3"]

    # executing again asks whether to keep the inputs
    run_and_wait(w, qapp)
    assert len(asked_reuse) == 1
    assert "Command-line arguments: 3" in asked_reuse[0] and "Keyboard input: 1 line (7)" in asked_reuse[0]
    assert len(asked_args) == 1 and len(asked_input) == 1, "kept inputs are not asked again"

    # ... or to reset them: then everything is asked again
    w.ask_reuse_inputs = lambda summary: "reset"
    run_and_wait(w, qapp)
    assert len(asked_args) == 2 and len(asked_input) == 2

    # cancel keeps the old run
    before = w.trace
    w.ask_reuse_inputs = lambda summary: "cancel"
    w.execute()
    assert not w.running and w.trace is before


def test_no_input_questions_for_programs_without_input(window, qapp):
    w = window
    load(w, "Function calls and stack frames")
    w.ask_reuse_inputs = lambda summary: (_ for _ in ()).throw(AssertionError("should not ask"))
    w.ask_args = lambda name, cur: (_ for _ in ()).throw(AssertionError("should not ask"))
    run_and_wait(w, qapp)
    run_and_wait(w, qapp)
    assert w.trace is not None


def test_edit_and_reset_inputs(window, qapp):
    w = window
    w.set_source(ARGS_AND_INPUT)
    w.ask_args = lambda name, cur: ["2"]
    w.ask_input = lambda req: ("line", "5")
    run_and_wait(w, qapp)
    assert w.inputs.locked
    w.unlock_inputs()
    assert not w.inputs.locked and not w.stdin_edit.isReadOnly()
    assert not w.stale, "unlocking alone does not change the run"
    w.stdin_edit.setPlainText("9\n")
    assert w.stale and not w.act_play.isEnabled(), "changed inputs make the run outdated"
    # edited inputs are the user's choice: no reuse question, and they are used as they are
    w.ask_reuse_inputs = lambda summary: (_ for _ in ()).throw(AssertionError("should not ask"))
    run_and_wait(w, qapp)
    w.goto(w.last_step())
    assert w.output.toPlainText().startswith("9\n9\n")

    w.reset_inputs()
    assert w.inputs.args() == [] and w.stdin_edit.toPlainText() == "" and not w.inputs.args_confirmed
    assert not w.inputs.locked


def test_arguments_typed_in_the_panel_are_used_without_asking(window, qapp):
    w = window
    w.set_source(ARGS_AND_INPUT, "4\n")
    w.inputs.args_editor.add_argument()
    w.inputs.args_editor.table.item(1, 0).setText("2")
    assert w.inputs.args_confirmed
    w.ask_args = lambda name, cur: (_ for _ in ()).throw(AssertionError("should not ask"))
    run_and_wait(w, qapp)
    assert w.trace.args == ["./prog", "2"]


def test_program_name_follows_the_file_name(window, qapp, tmp_path):
    w = window
    path = tmp_path / "hello.c"
    path.write_text(ARGS_AND_INPUT, encoding="utf-8")
    w.open_file(str(path))
    assert w.prog_name() == "./hello"
    run_and_wait(w, qapp)
    assert w.trace.args[0] == "./hello"


def test_inputs_are_saved_with_the_settings(window, qapp):
    w = window
    w.set_source(ARGS_AND_INPUT)
    w.ask_args = lambda name, cur: ["a", "b c"]
    w.ask_input = lambda req: ("line", "1")
    run_and_wait(w, qapp)
    w.save_settings()
    s = w.settings
    assert list(s.value("args")) == ["a", "b c"]
    assert s.value("inputs_locked") in (True, "true")
    assert s.value("args_confirmed") in (True, "true")


def test_command_line_example_suggests_its_arguments(window, qapp):
    w = window
    ex = next(e for e in __import__("stacksim.gui.examples", fromlist=["EXAMPLES"]).EXAMPLES if e.title == "Command-line arguments")
    w.load_example(ex)
    seen = []
    w.ask_args = lambda name, cur: seen.append(list(cur)) or list(cur)
    run_and_wait(w, qapp)
    assert seen == [["3", "hello"]], "the dialog is still shown, pre-filled with the suggestion"
    assert w.trace.args == ["./prog", "3", "hello"]


def test_dark_theme_by_default_and_switchable(window, qapp):
    w = window
    assert w.theme_name == "dark" and w.theme.dark
    assert qapp.palette().color(qapp.palette().ColorRole.Window).lightness() < 128
    assert w.theme_actions["dark"].isChecked()
    assert w.theme.tb_border.name() in w.toolbar.styleSheet()
    assert w.toolbar.widgetForAction(w.act_execute).objectName() == "executeButton"

    w.set_theme("light")
    assert not w.theme.dark and not w.canvas.theme.dark and not w.editor.theme.dark
    assert qapp.palette().color(qapp.palette().ColorRole.Window).lightness() >= 128
    assert w.theme.tb_border.name() in w.toolbar.styleSheet()
    w.save_settings()
    assert w.settings.value("theme") == "light"
    w.set_theme("dark")
    assert w.theme.dark

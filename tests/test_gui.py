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

    w.speed.setCurrentIndex(5)  # very fast
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

"""The main window: editor, input/output, problems, playback and the memory view."""

from __future__ import annotations

import html
import threading
import traceback
from importlib import resources

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QStyle,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..c.compiler import CompileResult, compile_source
from ..viewmodel import MemoryState, ViewBuilder
from ..vm.interpreter import DEFAULT_MAX_STEPS, execute
from ..vm.trace import Trace
from .canvas import MemoryCanvas
from .editor import CodeEditor
from .examples import DEFAULT_PROGRAM, EXAMPLES, NEW_FILE
from .theme import current_theme, mono_font
from .timeline import Timeline

SPEEDS = [
    ("Step by step (manual)", 0.0),
    ("Very slow (1 step/s)", 1.0),
    ("Slow (2 steps/s)", 2.0),
    ("Normal (4 steps/s)", 4.0),
    ("Fast (10 steps/s)", 10.0),
    ("Very fast (30 steps/s)", 30.0),
    ("Instant (jump to the end)", -1.0),
]
DEFAULT_SPEED = 3

STEP_KIND_TEXT = {
    "start": "the program starts",
    "call": "a function was called",
    "return": "a function is returning",
    "end": "a function reached its end",
    "loop": "back at the loop header",
    "exit": "the program has finished",
    "error": "the program crashed",
    "limit": "the program was stopped",
}


class _Bridge(QObject):
    finished = Signal(object, object, object)  # CompileResult, Trace | None, error text | None


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings("stacksim", "stacksim")
        self.theme = current_theme()
        self.trace: Trace | None = None
        self.builder: ViewBuilder | None = None
        self.compile_result: CompileResult | None = None
        self.state: MemoryState | None = None
        self.pos = 0
        self.stale = False
        self.running = False
        self.cancel: threading.Event | None = None
        self.direction = 1
        self.play_origin = 0
        self.out_sig: tuple = (0, None)
        self.file_path: str | None = None
        self.max_steps = int(self.settings.value("max_steps", DEFAULT_MAX_STEPS))
        self.bridge = _Bridge()
        self.bridge.finished.connect(self.on_run_finished)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

        self.build_ui()
        self.build_actions()
        self.restore()
        self.update_controls()
        self.update_title()

    # ================================================================== UI

    def build_ui(self) -> None:
        self.setWindowTitle("stacksim")
        self.resize(1500, 900)

        # Editor with a small header.
        self.editor = CodeEditor()
        self.editor.textChanged.connect(self.on_code_changed)
        self.editor.breakpointsChanged.connect(self.on_breakpoints_changed)
        self.editor.document().modificationChanged.connect(lambda _: self.update_title())
        editor_box = QWidget()
        lay = QVBoxLayout(editor_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.editor_header = QLabel()
        self.editor_header.setContentsMargins(6, 4, 6, 0)
        lay.addWidget(self.editor_header)
        lay.addWidget(self.editor)

        # Output / input / problems.
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono_font(10))
        self.output.setPlaceholderText("What the program prints appears here.")
        self.stdin_edit = QPlainTextEdit()
        self.stdin_edit.setFont(mono_font(10))
        self.stdin_edit.setPlaceholderText("Type the input for scanf / getchar / fgets here before pressing Run.")
        self.stdin_edit.textChanged.connect(self.on_code_changed)
        stdin_box = QWidget()
        sl = QVBoxLayout(stdin_box)
        sl.setContentsMargins(4, 4, 4, 4)
        hint = QLabel("The program reads this text as its keyboard input (stdin). Text it has already read is greyed out.")
        hint.setWordWrap(True)
        sl.addWidget(hint)
        sl.addWidget(self.stdin_edit)
        self.problems = QTreeWidget()
        self.problems.setHeaderLabels(["", "Line", "Step", "Problem"])
        self.problems.setRootIsDecorated(False)
        self.problems.setColumnWidth(0, 28)
        self.problems.setColumnWidth(1, 50)
        self.problems.setColumnWidth(2, 60)
        self.problems.itemActivated.connect(self.on_problem_clicked)
        self.problems.itemClicked.connect(self.on_problem_clicked)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.output, "Output")
        self.tabs.addTab(stdin_box, "Input (stdin)")
        self.tabs.addTab(self.problems, "Problems")

        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.addWidget(editor_box)
        self.left_split.addWidget(self.tabs)
        self.left_split.setStretchFactor(0, 3)
        self.left_split.setStretchFactor(1, 1)

        # Explanation + memory canvas.
        self.explain = QTextBrowser()
        self.explain.setOpenLinks(False)
        self.explain.setMinimumHeight(60)
        self.canvas = MemoryCanvas()
        self.canvas.lineHovered.connect(lambda line: self.editor.set_hover_line(line))
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.right_split = QSplitter(Qt.Orientation.Vertical)
        self.right_split.addWidget(self.explain)
        self.right_split.addWidget(self.scroll)
        self.right_split.setStretchFactor(0, 1)
        self.right_split.setStretchFactor(1, 4)

        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        self.main_split.addWidget(self.left_split)
        self.main_split.addWidget(self.right_split)
        self.main_split.setStretchFactor(0, 2)
        self.main_split.setStretchFactor(1, 3)

        # Timeline bar.
        self.timeline = Timeline()
        self.timeline.seek.connect(self.on_seek)
        self.step_label = QLabel("no run yet")
        self.step_label.setMinimumWidth(150)
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 2, 8, 4)
        bl.addWidget(QLabel("Timeline:"))
        bl.addWidget(self.timeline, 1)
        bl.addWidget(self.step_label)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.main_split, 1)
        cl.addWidget(bar)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready. Press F5 to run the program.")
        self.show_intro()

    def build_actions(self) -> None:
        st = self.style()

        def act(text, slot, shortcut=None, icon=None, tip=None, checkable=False):
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(st.standardIcon(icon))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            if tip or shortcut:
                a.setToolTip(f"{tip or text}" + (f"  ({QKeySequence(shortcut).toString()})" if shortcut else ""))
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            return a

        S = QStyle.StandardPixmap
        self.act_run = act("Run", self.run, "F5", S.SP_MediaPlay, "Compile and run the program, recording every step")
        self.act_stop = act("Stop", self.stop_run, "Shift+F5", S.SP_MediaStop, "Stop a run that takes too long")
        self.act_first = act("First step", lambda: self.goto(0, pause=True), "Alt+Home", S.SP_MediaSkipBackward, "Go to the first step")
        self.act_back_over = act("Back over", self.step_back_over, "Shift+F11", None, "Go back to the previous line of the current function (skipping over calls)")
        self.act_back = act("Back", self.step_back, "Shift+F10", S.SP_ArrowBack, "Go one step back")
        self.act_play_back = act("Play backwards", lambda: self.toggle_play(-1), "Shift+F6", S.SP_MediaSeekBackward, "Play backwards")
        self.act_play = act("Play", lambda: self.toggle_play(1), "F6", S.SP_MediaPlay, "Play / pause")
        self.act_step = act("Step", self.step_forward, "F10", S.SP_ArrowForward, "Go one step forward")
        self.act_step_over = act("Step over", self.step_over, "F11", None, "Go to the next line of the current function (skipping over calls)")
        self.act_last = act("Last step", lambda: self.goto(self.last_step(), pause=True), "Alt+End", S.SP_MediaSkipForward, "Go to the last step")

        tb = QToolBar("Run")
        tb.setObjectName("runbar")
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(self.act_run)
        tb.addAction(self.act_stop)
        tb.addSeparator()
        for a in (self.act_first, self.act_back_over, self.act_back, self.act_play_back, self.act_play, self.act_step, self.act_step_over, self.act_last):
            tb.addAction(a)
        tb.addSeparator()
        tb.addWidget(QLabel(" Speed: "))
        self.speed = QComboBox()
        for name, _ in SPEEDS:
            self.speed.addItem(name)
        self.speed.setCurrentIndex(DEFAULT_SPEED)
        self.speed.setToolTip("How fast Play moves through the steps. 'Instant' jumps straight to the end (or the next breakpoint).")
        self.speed.currentIndexChanged.connect(self.on_speed_changed)
        tb.addWidget(self.speed)

        # Menus.
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(act("New", self.new_file, "Ctrl+N"))
        m.addAction(act("Open...", self.open_file_dialog, "Ctrl+O"))
        m.addAction(act("Save", self.save_file, "Ctrl+S"))
        m.addAction(act("Save as...", self.save_file_as, "Ctrl+Shift+S"))
        m.addSeparator()
        m.addAction(act("Quit", self.close, "Ctrl+Q"))

        em = mb.addMenu("&Examples")
        groups: dict[str, object] = {}
        for ex in EXAMPLES:
            sub = groups.get(ex.group)
            if sub is None:
                sub = em.addMenu(ex.group)
                groups[ex.group] = sub
            sub.addAction(act(ex.title, lambda checked=False, e=ex: self.load_example(e)))

        rm = mb.addMenu("&Run")
        for a in (self.act_run, self.act_stop):
            rm.addAction(a)
        rm.addSeparator()
        for a in (self.act_play, self.act_play_back, self.act_step, self.act_back, self.act_step_over, self.act_back_over, self.act_first, self.act_last):
            rm.addAction(a)
        rm.addSeparator()
        rm.addAction(act("Toggle breakpoint", self.toggle_breakpoint_at_cursor, "F9"))
        rm.addAction(act("Clear all breakpoints", self.clear_breakpoints))
        rm.addAction(act("Step limit...", self.ask_step_limit))

        vm = mb.addMenu("&View")
        self.act_addr = act("Show addresses", lambda c: self.set_canvas_option("show_addresses", c), checkable=True)
        self.act_bytes = act("Show raw bytes", lambda c: self.set_canvas_option("show_bytes", c), checkable=True)
        self.act_freed = act("Show freed heap blocks", lambda c: self.set_canvas_option("show_freed", c), checkable=True)
        self.act_frames = act(
            "Show return addresses",
            lambda c: self.set_canvas_option("show_frame_info", c),
            tip="Show the return address and the saved frame pointer at the top of every stack frame",
            checkable=True,
        )
        for a in (self.act_addr, self.act_bytes, self.act_freed, self.act_frames):
            vm.addAction(a)
        vm.addSeparator()
        vm.addAction(act("Zoom in", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.15), "Ctrl++"))
        vm.addAction(act("Zoom out", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.15), "Ctrl+-"))
        vm.addAction(act("Reset zoom", lambda: self.canvas.set_zoom(1.0), "Ctrl+0"))

        tb.addSeparator()
        for a in (self.act_addr, self.act_bytes, self.act_freed, self.act_frames):
            tb.addAction(a)

        hm = mb.addMenu("&Help")
        hm.addAction(act("How to read the memory view", lambda: self.show_doc("memory_view.md", "How to read the memory view")))
        hm.addAction(act("Supported C and library functions", lambda: self.show_doc("supported_c.md", "Supported C")))
        hm.addAction(act("Keyboard shortcuts", self.show_shortcuts))
        hm.addSeparator()
        hm.addAction(act("About stacksim", self.show_about))

    # ============================================================ settings

    def restore(self) -> None:
        s = self.settings
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        for key, split in (("split_main", self.main_split), ("split_left", self.left_split), ("split_right", self.right_split)):
            v = s.value(key)
            if v is not None:
                split.restoreState(v)
        if s.value("split_main") is None:
            self.main_split.setSizes([600, 900])
            self.left_split.setSizes([620, 220])
            self.right_split.setSizes([150, 700])
        src = s.value("source")
        self.editor.setPlainText(src if isinstance(src, str) and src.strip() else DEFAULT_PROGRAM)
        self.stdin_edit.setPlainText(s.value("stdin", "") or "")
        self.file_path = s.value("file_path") or None
        self.speed.setCurrentIndex(int(s.value("speed", DEFAULT_SPEED)))

        def flag(name, default):
            v = s.value(name, default)
            return v in (True, "true", "1", 1)

        self.act_addr.setChecked(flag("show_addresses", True))
        self.act_bytes.setChecked(flag("show_bytes", False))
        self.act_freed.setChecked(flag("show_freed", True))
        self.act_frames.setChecked(flag("show_frame_info", False))
        self.canvas.show_addresses = self.act_addr.isChecked()
        self.canvas.show_bytes = self.act_bytes.isChecked()
        self.canvas.show_frame_info = self.act_frames.isChecked()
        self.show_freed = self.act_freed.isChecked()
        self.editor.document().setModified(False)
        self.stale = False

    def save_settings(self) -> None:
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("split_main", self.main_split.saveState())
        s.setValue("split_left", self.left_split.saveState())
        s.setValue("split_right", self.right_split.saveState())
        s.setValue("source", self.editor.toPlainText())
        s.setValue("stdin", self.stdin_edit.toPlainText())
        s.setValue("file_path", self.file_path or "")
        s.setValue("speed", self.speed.currentIndex())
        s.setValue("show_addresses", self.act_addr.isChecked())
        s.setValue("show_bytes", self.act_bytes.isChecked())
        s.setValue("show_freed", self.act_freed.isChecked())
        s.setValue("show_frame_info", self.act_frames.isChecked())
        s.setValue("max_steps", self.max_steps)

    def closeEvent(self, ev) -> None:
        if self.file_path and self.editor.document().isModified():
            r = QMessageBox.question(
                self,
                "Unsaved changes",
                f"Save the changes to {self.file_path}?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            )
            if r == QMessageBox.StandardButton.Cancel:
                ev.ignore()
                return
            if r == QMessageBox.StandardButton.Save and not self.save_file():
                ev.ignore()
                return
        if self.cancel is not None:
            self.cancel.set()
        self.save_settings()
        ev.accept()

    # ================================================================ files

    def update_title(self) -> None:
        name = self.file_path.replace("\\", "/").rsplit("/", 1)[-1] if self.file_path else "untitled.c"
        mod = " *" if self.editor.document().isModified() and self.file_path else ""
        self.setWindowTitle(f"{name}{mod} - stacksim")
        self.editor_header.setText(f"<b>{html.escape(name)}</b>{mod}  <span style='color:gray'>- click in the margin to set a breakpoint</span>")

    def confirm_discard(self) -> bool:
        if self.file_path and self.editor.document().isModified():
            r = QMessageBox.question(
                self, "Unsaved changes", "Discard the changes to the current file?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            )
            return r == QMessageBox.StandardButton.Discard
        return True

    def set_source(self, text: str, stdin: str = "", path: str | None = None) -> None:
        self.pause()
        self.editor.setPlainText(text)
        self.stdin_edit.setPlainText(stdin)
        self.file_path = path
        self.editor.document().setModified(False)
        self.clear_run("Press Run (F5) to start the program.\nThe stack and the heap will appear here.")
        self.update_title()

    def new_file(self) -> None:
        if self.confirm_discard():
            self.set_source(NEW_FILE)

    def load_example(self, ex) -> None:
        if self.confirm_discard():
            self.set_source(ex.code, ex.stdin)
            self.statusBar().showMessage(f"Loaded example: {ex.title}. Press F5 to run it.")

    def open_file_dialog(self) -> None:
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open C file", self.file_path or "", "C files (*.c *.h);;All files (*)")
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            QMessageBox.warning(self, "Open failed", str(e))
            return
        self.set_source(text, self.stdin_edit.toPlainText(), path)

    def save_file(self) -> bool:
        if not self.file_path:
            return self.save_file_as()
        try:
            with open(self.file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.editor.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return False
        self.editor.document().setModified(False)
        self.update_title()
        self.statusBar().showMessage(f"Saved {self.file_path}", 3000)
        return True

    def save_file_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save C file", self.file_path or "main.c", "C files (*.c);;All files (*)")
        if not path:
            return False
        self.file_path = path
        return self.save_file()

    # ================================================================= run

    def run(self) -> None:
        if self.running:
            return
        self.pause()
        src = self.editor.toPlainText()
        stdin = self.stdin_edit.toPlainText().encode("utf-8")
        result = compile_source(src)
        self.compile_result = result
        if not result.ok:
            self.show_compile_errors(result)
            return
        self.running = True
        self.cancel = threading.Event()
        self.update_controls()
        self.statusBar().showMessage("Running... (the whole run is recorded, then you can move through it)")
        cancel = self.cancel
        max_steps = self.max_steps

        def work():
            try:
                trace = execute(result.program, stdin, max_steps, cancel, src)
                self.bridge.finished.emit(result, trace, None)
            except Exception:  # pragma: no cover - internal errors are shown to the user
                self.bridge.finished.emit(result, None, traceback.format_exc())

        threading.Thread(target=work, name="stacksim-run", daemon=True).start()

    def stop_run(self) -> None:
        if self.cancel is not None:
            self.cancel.set()

    def on_run_finished(self, result: CompileResult, trace: Trace | None, err: str | None) -> None:
        self.running = False
        self.cancel = None
        if err is not None or trace is None:
            self.update_controls()
            QMessageBox.critical(self, "Internal error", "stacksim ran into an internal error while running the program:\n\n" + (err or "unknown"))
            self.statusBar().showMessage("Internal error.")
            return
        self.load_trace(result, trace)
        speed = SPEEDS[self.speed.currentIndex()][1]
        if speed < 0:
            self.goto(self.find_stop(0, 1))
        else:
            self.goto(0)
            if speed > 0:
                self.toggle_play(1)
        end = {"exit": "finished", "error": "crashed", "limit": "was stopped (step limit)", "cancelled": "was stopped"}[trace.end_reason]
        nwarn = sum(1 for d in trace.diagnostics if d.severity == "warning") + sum(1 for d in result.diagnostics if d.severity == "warning")
        self.statusBar().showMessage(
            f"The program {end} after {len(trace.steps)} steps"
            + (f" - {nwarn} problem{'s' if nwarn != 1 else ''} found (see the Problems tab)" if nwarn else "")
            + ". Use the playback buttons or the timeline to move through it."
        )

    def show_compile_errors(self, result: CompileResult) -> None:
        self.trace = None
        self.builder = None
        self.state = None
        self.stale = False
        self.canvas.stale = False
        self.canvas.placeholder = "The program could not be compiled.\nFix the error shown in the Problems tab and press Run again."
        self.canvas.set_state(None)
        self.timeline.set_trace(0, [], [])
        self.step_label.setText("compile error")
        self.editor.set_exec_lines(None, None)
        diags = result.diagnostics
        self.editor.set_diagnostics([(d.line, d.loc.col if d.loc else 0, d.loc.length if d.loc else 1, d.severity, d.message) for d in diags if d.line])
        self.fill_problems(diags, [])
        self.output.clear()
        self.out_sig = (0, None)
        cur = self.output.textCursor()
        fmt = QTextCharFormat()
        fmt.setForeground(self.theme.out_stderr)
        for d in diags:
            if d.severity == "error":
                cur.insertText(d.format() + "\n", fmt)
        err = result.errors[0]
        self.explain.setHtml(
            f"<h3 style='color:{self.theme.out_stderr.name()}'>The program could not be compiled</h3>"
            f"<p><b>Line {err.line or '?'}:</b> {html.escape(err.message)}</p>"
            + (f"<p><i>Hint: {html.escape(err.hint)}</i></p>" if err.hint else "")
            + "<p>Before C code can run, it is checked and translated. Fix the problem and press <b>Run</b> again.</p>"
        )
        self.tabs.setCurrentIndex(2)
        if err.line:
            block = self.editor.document().findBlockByNumber(err.line - 1)
            c = QTextCursor(block)
            if err.loc and err.loc.col:
                c.setPosition(block.position() + min(err.loc.col - 1, max(0, block.length() - 1)))
            self.editor.setTextCursor(c)
            self.editor.setFocus()
        self.update_controls()
        self.statusBar().showMessage("Compilation failed.")

    def load_trace(self, result: CompileResult, trace: Trace) -> None:
        self.trace = trace
        self.builder = ViewBuilder(trace)
        self.stale = False
        self.canvas.stale = False
        self.out_sig = (-1, None)
        marks = []
        for d in trace.diagnostics:
            if d.step is not None and d.severity in ("warning", "error"):
                marks.append((min(d.step, len(trace.steps) - 1), d.severity, d.message))
        self.timeline.set_trace(len(trace.steps), marks, self.breakpoint_steps())
        self.fill_problems(result.diagnostics, trace.diagnostics)
        self.tabs.setCurrentIndex(0)

    def clear_run(self, placeholder: str) -> None:
        self.trace = None
        self.builder = None
        self.state = None
        self.stale = False
        self.canvas.stale = False
        self.canvas.placeholder = placeholder
        self.canvas.set_state(None)
        self.timeline.set_trace(0, [], [])
        self.step_label.setText("no run yet")
        self.editor.set_exec_lines(None, None)
        self.editor.set_diagnostics([])
        self.problems.clear()
        self.output.clear()
        self.out_sig = (0, None)
        self.show_intro()
        self.update_controls()

    def on_code_changed(self) -> None:
        if self.trace is not None and not self.stale:
            self.stale = True
            self.pause()
            self.canvas.stale = True
            self.canvas.update()
            self.editor.set_exec_lines(None, None)
            self.explain.setHtml(
                "<h3>The code or input has changed</h3><p>The memory picture belongs to the previous version. "
                "Press <b>Run</b> (F5) to run the new version.</p>"
            )
            self.update_controls()

    # ============================================================ problems

    def fill_problems(self, compile_diags, run_diags) -> None:
        self.problems.clear()
        st = self.style()
        icons = {
            "error": st.standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical),
            "warning": st.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning),
            "note": st.standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation),
        }
        for d in list(compile_diags) + list(run_diags):
            text = d.message + (f"\nHint: {d.hint}" if d.hint else "")
            it = QTreeWidgetItem(["", str(d.line or ""), "" if d.step is None else str(d.step + 1), text])
            it.setIcon(0, icons.get(d.severity, icons["note"]))
            it.setToolTip(3, text)
            it.setData(0, Qt.ItemDataRole.UserRole, (d.step, d.line))
            self.problems.addTopLevelItem(it)
        n = self.problems.topLevelItemCount()
        self.tabs.setTabText(2, f"Problems ({n})" if n else "Problems")

    def on_problem_clicked(self, item: QTreeWidgetItem, col: int = 0) -> None:
        step, line = item.data(0, Qt.ItemDataRole.UserRole)
        if step is not None and self.trace is not None and not self.stale:
            self.goto(min(step, self.last_step()), pause=True)
        elif line:
            block = self.editor.document().findBlockByNumber(line - 1)
            self.editor.setTextCursor(QTextCursor(block))
            self.editor.setFocus()

    # ============================================================ playback

    def last_step(self) -> int:
        return len(self.trace.steps) - 1 if self.trace else 0

    def can_play(self) -> bool:
        return self.trace is not None and not self.stale and not self.running

    def update_controls(self) -> None:
        ok = self.can_play()
        last = self.last_step()
        playing = self.timer.isActive()
        for a in (self.act_first, self.act_back, self.act_back_over):
            a.setEnabled(ok and self.pos > 0)
        for a in (self.act_last, self.act_step, self.act_step_over):
            a.setEnabled(ok and self.pos < last)
        self.act_play.setEnabled(ok)
        self.act_play_back.setEnabled(ok)
        st = self.style()
        if playing and self.direction > 0:
            self.act_play.setText("Pause")
            self.act_play.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.act_play.setText("Play")
            self.act_play.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        if playing and self.direction < 0:
            self.act_play_back.setText("Pause")
            self.act_play_back.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.act_play_back.setText("Play backwards")
            self.act_play_back.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
        self.act_run.setEnabled(not self.running)
        self.act_stop.setEnabled(self.running)

    def on_speed_changed(self, idx: int) -> None:
        if self.timer.isActive():
            speed = SPEEDS[idx][1]
            if speed > 0:
                self.timer.setInterval(int(1000 / speed))
            else:
                d = self.direction
                self.pause()
                if speed < 0:
                    self.goto(self.find_stop(self.pos, d))

    def toggle_play(self, direction: int) -> None:
        if not self.can_play():
            return
        if self.timer.isActive():
            same = self.direction == direction
            self.pause()
            if same:
                return
        speed = SPEEDS[self.speed.currentIndex()][1]
        if speed < 0:
            self.goto(self.find_stop(self.pos, direction))
            return
        if speed == 0:
            speed = 4.0
        if direction > 0 and self.pos >= self.last_step():
            self.goto(0)
        if direction < 0 and self.pos <= 0:
            return
        self.direction = direction
        self.play_origin = self.pos
        self.timer.start(int(1000 / speed))
        self.update_controls()

    def pause(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
        self.update_controls()

    def tick(self) -> None:
        if not self.can_play():
            self.pause()
            return
        nxt = self.pos + self.direction
        if nxt < 0 or nxt > self.last_step():
            self.pause()
            return
        self.goto(nxt)
        if self.is_breakpoint(nxt) or nxt in (0, self.last_step()):
            self.pause()

    def is_breakpoint(self, step: int) -> bool:
        bps = self.editor.breakpoints
        if not bps or self.trace is None:
            return False
        st = self.trace.steps[step]
        return st.line in bps and st.kind in ("stmt", "loop", "start", "call", "end")

    def find_stop(self, start: int, direction: int) -> int:
        """The next breakpoint step in ``direction`` (or the first/last step)."""
        last = self.last_step()
        i = start + direction
        while 0 <= i <= last:
            if self.is_breakpoint(i):
                return i
            i += direction
        return last if direction > 0 else 0

    def step_forward(self) -> None:
        if self.can_play():
            self.goto(self.pos + 1, pause=True)

    def step_back(self) -> None:
        if self.can_play():
            self.goto(self.pos - 1, pause=True)

    def depth(self, step: int) -> int:
        st = self.trace.steps[step]
        return self.trace.frames[st.frame].depth if st.frame is not None else -1

    def step_over(self) -> None:
        if not self.can_play():
            return
        d = self.depth(self.pos)
        j = self.pos + 1
        last = self.last_step()
        while j < last and self.depth(j) > d:
            j += 1
        self.goto(j, pause=True)

    def step_back_over(self) -> None:
        if not self.can_play():
            return
        d = self.depth(self.pos)
        j = self.pos - 1
        while j > 0 and self.depth(j) > d:
            j -= 1
        self.goto(j, pause=True)

    def on_seek(self, step: int) -> None:
        if self.can_play():
            self.goto(step, pause=True)

    def on_breakpoints_changed(self, bps) -> None:
        if self.trace is not None:
            self.timeline.set_trace(len(self.trace.steps), self.timeline.marks, self.breakpoint_steps())

    def breakpoint_steps(self) -> list[int]:
        if self.trace is None or not self.editor.breakpoints:
            return []
        return [i for i in range(len(self.trace.steps)) if self.is_breakpoint(i)]

    def toggle_breakpoint_at_cursor(self) -> None:
        self.editor.toggle_breakpoint(self.editor.textCursor().blockNumber() + 1)

    def clear_breakpoints(self) -> None:
        self.editor.breakpoints.clear()
        self.editor.gutter.update()
        self.on_breakpoints_changed(set())

    def ask_step_limit(self) -> None:
        v, ok = QInputDialog.getInt(
            self, "Step limit",
            "Stop the program after this many steps\n(protects against endless loops):",
            self.max_steps, 1000, 1_000_000, 1000,
        )
        if ok:
            self.max_steps = v

    def set_canvas_option(self, name: str, value: bool) -> None:
        if name == "show_freed":
            self.show_freed = value
            if self.trace is not None:
                self.goto(self.pos)
            return
        self.canvas.set_option(name, value)
        if name == "show_frame_info" and self.state is not None and not self.stale:
            self.update_explanation(self.state)

    # ================================================================ goto

    def goto(self, step: int, pause: bool = False) -> None:
        if self.trace is None:
            return
        if pause:
            self.pause()
        step = max(0, min(step, self.last_step()))
        self.pos = step
        state = self.builder.build(step, show_freed=self.show_freed)
        self.state = state
        self.canvas.set_state(state)
        self.editor.set_exec_lines(state.line, state.prev_line, error=state.kind == "error")
        self.update_editor_diagnostics(step)
        self.update_output(step)
        self.update_stdin(step)
        self.update_explanation(state)
        self.timeline.set_position(step)
        self.step_label.setText(f"Step {step + 1} / {len(self.trace.steps)}")
        self.update_controls()
        self.reveal_top_frame(state)
        self.reveal_problem(state)

    def reveal_top_frame(self, state: MemoryState) -> None:
        """When a function is called or returns, scroll so that the newest frame is visible."""
        lay = self.canvas.layout_
        top = state.frames[-1].id if state.frames else None
        if lay is None or top is None or top == getattr(self, "_last_top", None):
            self._last_top = top
            return
        self._last_top = top
        r = lay.frame_rects.get(top)
        if r is not None:
            z = self.canvas.zoom
            self.scroll.ensureVisible(int(r.left() * z), int(r.bottom() * z), 20, 20)
            self.scroll.ensureVisible(int(r.left() * z), int(r.top() * z), 20, 20)

    def reveal_problem(self, state: MemoryState) -> None:
        """Scroll the canvas so that memory involved in a problem is visible."""
        lay = self.canvas.layout_
        if lay is None or not state.problem_ranges:
            return
        for addr, size, r, _ in lay.anchors:
            for a, n in state.problem_ranges:
                if addr <= a < addr + max(size, 1):
                    z = self.canvas.zoom
                    self.scroll.ensureVisible(int(r.center().x() * z), int(r.center().y() * z), 80, 80)
                    return

    def update_editor_diagnostics(self, step: int) -> None:
        items = []
        if self.compile_result is not None:
            for d in self.compile_result.diagnostics:
                if d.line:
                    items.append((d.line, d.loc.col, d.loc.length, d.severity, d.message))
        for d in self.trace.diagnostics:
            if d.line and d.step is not None and d.step <= step and d.severity != "note":
                items.append((d.line, 0, 0, d.severity, d.message))
        self.editor.set_diagnostics(items)

    def update_output(self, step: int) -> None:
        tr = self.trace
        st = tr.steps[step]
        n = st.out_len
        final = step == self.last_step()
        footer = None
        if final:
            if tr.end_reason == "exit":
                footer = ("system", f"\n--- the program finished with exit code {tr.exit_code if tr.exit_code is not None else 0} ---\n")
            elif tr.end_reason == "error" and tr.fatal is not None:
                footer = ("stderr", f"\n--- the program crashed: {tr.fatal.message} ---\n")
            elif tr.end_reason == "limit":
                footer = ("system", f"\n--- stopped after {len(tr.steps) - 1} steps (step limit) ---\n")
            else:
                footer = ("system", "\n--- stopped ---\n")
        shown_n, shown_footer = self.out_sig
        cur = QTextCursor(self.output.document())
        if shown_footer is None and n >= shown_n >= 0:
            start = shown_n
        else:
            self.output.clear()
            start = 0
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmts = {}
        for kind, color in (("stdout", self.theme.text), ("stderr", self.theme.out_stderr), ("system", self.theme.out_system)):
            f = QTextCharFormat()
            f.setForeground(color)
            fmts[kind] = f
        if start < n:
            groups: list[list] = []
            for stream, data in tr.output[start:n]:
                if groups and groups[-1][0] == stream:
                    groups[-1][1].append(data)
                else:
                    groups.append([stream, [data]])
            for stream, parts in groups:
                cur.insertText(b"".join(parts).decode("utf-8", "replace"), fmts.get(stream, fmts["stdout"]))
        if footer is not None:
            cur.insertText(footer[1], fmts[footer[0]])
        self.out_sig = (n, footer)
        self.output.setTextCursor(cur)
        self.output.ensureCursorVisible()

    def update_stdin(self, step: int) -> None:
        pos = self.trace.steps[step].stdin_pos
        text = self.stdin_edit.toPlainText()
        consumed = len(self.trace.stdin[:pos].decode("utf-8", "replace"))
        sel = QTextEdit.ExtraSelection()
        sel.format.setForeground(self.theme.uninit_text)
        sel.format.setBackground(self.theme.prev_line)
        c = QTextCursor(self.stdin_edit.document())
        c.setPosition(0)
        c.setPosition(min(consumed, len(text)), QTextCursor.MoveMode.KeepAnchor)
        sel.cursor = c
        self.stdin_edit.setExtraSelections([sel] if consumed else [])

    def source_line(self, line: int | None) -> str:
        if not line:
            return ""
        block = self.editor.document().findBlockByNumber(line - 1)
        return block.text().strip() if block.isValid() else ""

    def update_explanation(self, s: MemoryState) -> None:
        t = self.theme
        parts = []
        kind = STEP_KIND_TEXT.get(s.kind, "")
        parts.append(f"<b>Step {s.step + 1} of {s.total_steps}</b>" + (f" &nbsp;-&nbsp; {kind}" if kind else ""))
        if s.step > 0 and s.prev_line:
            parts.append(
                f"<div style='margin-top:4px'><span style='color:{t.text_dim.name()}'>Just ran line {s.prev_line}:</span> "
                f"<code>{html.escape(self.source_line(s.prev_line))}</code></div>"
            )
        if s.events:
            parts.append("<ul style='margin-top:2px; margin-bottom:2px'>" + "".join(f"<li>{html.escape(e)}</li>" for e in s.events) + "</ul>")
        for d in s.diagnostics:
            color = t.out_stderr.name() if d.severity == "error" else "#b26a00"
            label = {"error": "Error", "warning": "Problem", "note": "Note"}.get(d.severity, d.severity)
            parts.append(
                f"<div style='color:{color}; margin-top:4px'><b>{label}"
                + (f" (line {d.line})" if d.line else "")
                + f":</b> {html.escape(d.message)}</div>"
                + (f"<div style='color:{color}; margin-left:12px'><i>{html.escape(d.hint)}</i></div>" if d.hint else "")
            )
        if self.canvas.show_frame_info and s.frame_notes:
            parts.append(
                f"<div style='color:{t.frame_top_border.name()}; margin-top:4px'>"
                + "<br>".join(html.escape(n) for n in s.frame_notes)
                + "</div>"
            )
        if s.kind in ("exit", "error", "limit"):
            if s.kind == "exit":
                parts.append("<div style='margin-top:4px'><b>The program has ended.</b> Heap blocks that are still shown were never freed.</div>")
        elif s.line:
            parts.append(
                f"<div style='margin-top:4px'><span style='color:#2e9d3a'><b>Next: line {s.line}:</b></span> "
                f"<code>{html.escape(self.source_line(s.line))}</code></div>"
            )
        self.explain.setHtml("".join(parts))

    def show_intro(self) -> None:
        self.explain.setHtml(
            "<h3>Welcome to stacksim</h3>"
            "<p>Write a C program on the left (or pick one from the <b>Examples</b> menu) and press <b>Run</b> (F5). "
            "Every step of the program is recorded. Then move through the recording with <b>Play</b>, "
            "<b>Step</b> (F10), <b>Back</b> (Shift+F10) or the timeline, and watch the <b>stack</b> and the <b>heap</b> change.</p>"
        )

    # ================================================================ help

    def show_doc(self, name: str, title: str) -> None:
        try:
            text = resources.files("stacksim").joinpath("docs", name).read_text(encoding="utf-8")
        except OSError:
            text = f"# {title}\n\nThe documentation file {name} is missing."
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(820, 760)
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown(text)
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def show_shortcuts(self) -> None:
        rows = [
            ("F5", "Run (compile, run and record)"), ("Shift+F5", "Stop a long run"),
            ("F6", "Play / pause"), ("Shift+F6", "Play backwards"),
            ("F10", "One step forward"), ("Shift+F10", "One step back"),
            ("F11", "Step over (stay in the current function)"), ("Shift+F11", "Step back over"),
            ("Alt+Home / Alt+End", "First / last step"),
            ("F9", "Set or remove a breakpoint on the cursor line"),
            ("Ctrl + mouse wheel", "Zoom the memory view"), ("Click in the editor margin", "Set or remove a breakpoint"),
        ]
        QMessageBox.information(self, "Keyboard shortcuts", "\n".join(f"{k:<28} {v}" for k, v in rows))

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About stacksim",
            f"<h3>stacksim {__version__}</h3>"
            "<p>A teaching tool that runs C programs in a simulated computer and shows, step by step, "
            "what happens in the stack and the heap.</p>"
            "<p>The simulation is didactic: it is close to a typical 64-bit Linux system, "
            "but simplified where that makes memory easier to understand.</p>",
        )

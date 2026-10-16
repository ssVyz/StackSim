"""Program inputs: command-line arguments and keyboard input (stdin).

Inputs belong to an executed run. While a run exists they are locked, so that
playback always shows the outcome of exactly these inputs; Reset clears them
(the next Execute asks again) and Edit unlocks them for changes.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .theme import mono_font


class ArgsEditor(QWidget):
    """An editable list of command-line arguments: argv[0] (fixed) and argv[1], argv[2], ..."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self.locked = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(1, 1)
        self.table.setHorizontalHeaderLabels(["value"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setVisible(False)
        self.table.setFont(mono_font(10))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemChanged.connect(self._item_changed)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.add_btn = QPushButton("Add argument")
        self.add_btn.clicked.connect(self.add_argument)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove_argument)
        row.addWidget(self.add_btn)
        row.addWidget(self.remove_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.set_prog_name("./prog")

    def set_prog_name(self, name: str) -> None:
        self._updating = True
        item = QTableWidgetItem(name)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setForeground(QColor("#8a8f96"))
        item.setToolTip("argv[0] is the name of the program; it is set automatically.")
        self.table.setItem(0, 0, item)
        self._updating = False
        self._relabel()

    def _relabel(self) -> None:
        self.table.setVerticalHeaderLabels([f"argv[{i}]" for i in range(self.table.rowCount())])

    def args(self) -> list[str]:
        out = []
        for r in range(1, self.table.rowCount()):
            item = self.table.item(r, 0)
            out.append(item.text() if item is not None else "")
        return out

    def set_args(self, args: list[str]) -> None:
        self._updating = True
        self.table.setRowCount(1 + len(args))
        for i, a in enumerate(args):
            self.table.setItem(1 + i, 0, QTableWidgetItem(a))
        self._updating = False
        self._relabel()
        self._apply_lock()

    def add_argument(self) -> None:
        if self.locked:
            return
        r = self.table.rowCount()
        self._updating = True
        self.table.setRowCount(r + 1)
        self.table.setItem(r, 0, QTableWidgetItem(""))
        self._updating = False
        self._relabel()
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))
        self.changed.emit()

    def remove_argument(self) -> None:
        if self.locked:
            return
        r = self.table.currentRow()
        if r <= 0:
            r = self.table.rowCount() - 1
        if r >= 1:
            self.table.removeRow(r)
            self._relabel()
            self.changed.emit()

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        self._apply_lock()

    def _apply_lock(self) -> None:
        triggers = (
            QAbstractItemView.EditTrigger.NoEditTriggers
            if self.locked
            else QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setEditTriggers(triggers)
        self.add_btn.setEnabled(not self.locked)
        self.remove_btn.setEnabled(not self.locked)

    def _item_changed(self, item) -> None:
        if not self._updating:
            self.changed.emit()


class ArgsDialog(QDialog):
    """Asks for the command-line arguments before a program with argc/argv is executed."""

    def __init__(self, parent, prog_name: str, args: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Command-line arguments")
        lay = QVBoxLayout(self)
        label = QLabel(
            "<p><b>main()</b> of this program takes command-line arguments "
            "(<code>int argc, char *argv[]</code>).</p>"
            "<p>Enter the arguments for this run, one per row - like the words you would type after the program "
            f"name in a terminal: <code>{html.escape(prog_name)} 5 hello</code>. "
            "argv[0] is the program name and is set automatically. Leave the list empty for no arguments.</p>"
        )
        label.setWordWrap(True)
        lay.addWidget(label)
        self.editor = ArgsEditor()
        self.editor.set_prog_name(prog_name)
        self.editor.set_args(args)
        lay.addWidget(self.editor)
        note = QLabel("The arguments are saved in the <b>Input</b> tab and used again until you reset the inputs.")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox()
        ok = bb.addButton("Execute with these arguments", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        ok.setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(520, 380)
        if not args:
            self.editor.add_argument()

    def result_args(self) -> list[str]:
        # An argument row that was added but left empty does not count.
        args = self.editor.args()
        while args and args[-1] == "":
            args.pop()
        return args


class InputDialog(QDialog):
    """Asks for one line of keyboard input while a program is being executed."""

    def __init__(self, parent, req: dict):
        super().__init__(parent)
        self.setWindowTitle("Input needed")
        self.result_kind = "stop"
        lay = QVBoxLayout(self)
        where = f"Line {req['line']}: " if req.get("line") else ""
        text = f"<p>{where}<code>{html.escape(req.get('call') or '')}</code> is waiting for keyboard input.</p>"
        if req.get("output"):
            text += f"<p>The program has printed:<br><code>{html.escape(req['output'])}</code></p>"
        fmt = req.get("format")
        if fmt and fmt != fmt.rstrip():
            text += (
                "<p><i>Note: the format string ends with whitespace, so scanf keeps reading until it sees "
                "something that is not a space or a new line. Remove the whitespace at the end of the format.</i></p>"
            )
        label = QLabel(text)
        label.setWordWrap(True)
        lay.addWidget(label)
        self.edit = QLineEdit()
        self.edit.setFont(mono_font(11))
        self.edit.setPlaceholderText("Type one line, then press Enter")
        lay.addWidget(self.edit)
        note = QLabel(
            "Your input is saved in the <b>Input</b> tab and used again in the next executions, "
            "until you reset the inputs."
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox()
        ok = bb.addButton("OK", QDialogButtonBox.ButtonRole.AcceptRole)
        eof = bb.addButton("End of input (EOF)", QDialogButtonBox.ButtonRole.ActionRole)
        stop = bb.addButton("Stop program", QDialogButtonBox.ButtonRole.RejectRole)
        ok.setDefault(True)
        ok.clicked.connect(lambda: self._done("line"))
        eof.clicked.connect(lambda: self._done("eof"))
        stop.clicked.connect(lambda: self._done("stop"))
        eof.setToolTip("Tell the program that there is no more input (like Ctrl+D / Ctrl+Z in a terminal).")
        lay.addWidget(bb)
        self.edit.returnPressed.connect(lambda: self._done("line"))
        self.resize(480, self.sizeHint().height())

    def _done(self, kind: str) -> None:
        self.result_kind = kind
        self.accept()


class InputPanel(QWidget):
    """The Input tab: command-line arguments, keyboard input, and the lock."""

    changed = Signal()  # the user changed an input
    resetRequested = Signal()
    editRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.args_confirmed = False  # the user has decided on the arguments (an empty list counts)
        self.locked = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # Lock bar (shown while the inputs belong to an executed run).
        self.lock_bar = QFrame()
        self.lock_bar.setFrameShape(QFrame.Shape.StyledPanel)
        lb = QHBoxLayout(self.lock_bar)
        lb.setContentsMargins(6, 2, 6, 2)
        self.lock_label = QLabel(
            "<b>Locked:</b> the executed run used these inputs. Playback always shows the result for them."
        )
        self.lock_label.setWordWrap(True)
        lb.addWidget(self.lock_label, 1)
        self.edit_btn = QPushButton("Edit inputs")
        self.edit_btn.setToolTip("Unlock the inputs to change them (the run must be executed again afterwards)")
        self.edit_btn.clicked.connect(self.editRequested.emit)
        lb.addWidget(self.edit_btn)
        outer.addWidget(self.lock_bar)

        cols = QHBoxLayout()
        # Arguments
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Command-line arguments</b> (argc / argv)"))
        self.args_editor = ArgsEditor()
        self.args_editor.changed.connect(self._args_changed)
        left.addWidget(self.args_editor)
        cols.addLayout(left, 2)
        # Keyboard input
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Keyboard input</b> (stdin, for scanf / getchar / fgets) - text already read is greyed out"))
        self.stdin_edit = QPlainTextEdit()
        self.stdin_edit.setFont(mono_font(10))
        self.stdin_edit.setPlaceholderText(
            "Optional: type input here in advance. Otherwise Execute asks for it when the program reads it."
        )
        self.stdin_edit.textChanged.connect(self.changed.emit)
        right.addWidget(self.stdin_edit)
        row = QHBoxLayout()
        self.stdin_eof = QCheckBox("Input ends here (EOF)")
        self.stdin_eof.setToolTip("Checked: after this text the program gets 'end of input' (EOF) instead of asking you for more.")
        self.stdin_eof.toggled.connect(lambda _: self.changed.emit())
        row.addWidget(self.stdin_eof)
        row.addStretch(1)
        self.reset_btn = QPushButton("Reset inputs")
        self.reset_btn.setToolTip("Delete the arguments and the keyboard input; the next Execute asks for them again")
        self.reset_btn.clicked.connect(self.resetRequested.emit)
        row.addWidget(self.reset_btn)
        right.addLayout(row)
        cols.addLayout(right, 3)
        outer.addLayout(cols, 1)
        self.set_locked(False)

    def _args_changed(self) -> None:
        self.args_confirmed = True
        self.changed.emit()

    # ------------------------------------------------------------- state

    def args(self) -> list[str]:
        return self.args_editor.args()

    def set_args(self, args: list[str], confirmed: bool) -> None:
        self.args_editor.set_args(args)
        self.args_confirmed = confirmed

    def set_prog_name(self, name: str) -> None:
        self.args_editor.set_prog_name(name)

    def stdin_text(self) -> str:
        return self.stdin_edit.toPlainText()

    def eof(self) -> bool:
        return self.stdin_eof.isChecked()

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        self.lock_bar.setVisible(locked)
        self.stdin_edit.setReadOnly(locked)
        self.stdin_eof.setEnabled(not locked)
        self.args_editor.set_locked(locked)

    def reset(self) -> None:
        """Clear all inputs and unlock them."""
        self.set_locked(False)
        self.args_editor.set_args([])
        self.args_confirmed = False
        self.stdin_edit.clear()
        self.stdin_eof.setChecked(False)

    def has_saved(self, takes_args: bool, reads_stdin: bool) -> bool:
        """Are there inputs from earlier executions that this program would use?"""
        saved_stdin = bool(self.stdin_text()) or self.eof()
        return (takes_args and self.args_confirmed) or (reads_stdin and saved_stdin)

    def summary(self, takes_args: bool, reads_stdin: bool) -> str:
        parts = []
        if takes_args:
            args = self.args()
            if args:
                parts.append("Command-line arguments: " + " ".join(f'"{a}"' if (" " in a or not a) else a for a in args))
            else:
                parts.append("Command-line arguments: none")
        if reads_stdin:
            text = self.stdin_text()
            lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
            if lines:
                shown = text.strip("\n").replace("\n", " | ")
                if len(shown) > 60:
                    shown = shown[:57] + "..."
                parts.append(f"Keyboard input: {lines} line{'s' if lines != 1 else ''} ({shown})")
            else:
                parts.append("Keyboard input: none yet")
            if self.eof():
                parts.append("then end of input (EOF)")
        return "\n".join(parts)

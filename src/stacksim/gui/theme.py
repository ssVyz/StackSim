"""Colours and fonts, in a light and a dark variant."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


@dataclass
class Theme:
    dark: bool
    canvas: QColor
    text: QColor
    text_dim: QColor
    address: QColor
    frame_fill: QColor
    frame_border: QColor
    frame_header: QColor
    frame_top_border: QColor
    heap_fill: QColor
    heap_border: QColor
    heap_header: QColor
    freed_fill: QColor
    freed_border: QColor
    leak_fill: QColor
    leak_border: QColor
    static_fill: QColor
    static_border: QColor
    static_header: QColor
    box_fill: QColor
    box_border: QColor
    uninit_fill: QColor
    uninit_text: QColor
    changed_fill: QColor
    changed_border: QColor
    read_fill: QColor
    read_border: QColor
    problem_fill: QColor
    problem_border: QColor
    arrow: QColor
    arrow_bad: QColor
    null_text: QColor
    next_line: QColor
    prev_line: QColor
    error_line: QColor
    warning_line: QColor
    breakpoint: QColor
    gutter: QColor
    gutter_text: QColor
    syn_keyword: QColor
    syn_type: QColor
    syn_string: QColor
    syn_number: QColor
    syn_comment: QColor
    syn_preproc: QColor
    syn_function: QColor
    out_stderr: QColor
    out_system: QColor
    # toolbar buttons
    tb_button: QColor = None
    tb_border: QColor = None
    tb_hover: QColor = None
    tb_checked: QColor = None
    tb_accent: QColor = None


def _c(s: str) -> QColor:
    return QColor(s)


LIGHT = Theme(
    dark=False,
    canvas=_c("#fbfbf8"), text=_c("#202124"), text_dim=_c("#5f6368"), address=_c("#8a8f96"),
    frame_fill=_c("#eef3fb"), frame_border=_c("#a9bfe0"), frame_header=_c("#d9e5f6"), frame_top_border=_c("#2f6fd0"),
    heap_fill=_c("#edf7ef"), heap_border=_c("#93c4a0"), heap_header=_c("#d6eedc"),
    freed_fill=_c("#f2f2f2"), freed_border=_c("#bdbdbd"),
    leak_fill=_c("#fff3e3"), leak_border=_c("#e8710a"),
    static_fill=_c("#fcf6e6"), static_border=_c("#d8c27a"), static_header=_c("#f3e8c4"),
    box_fill=_c("#ffffff"), box_border=_c("#b4bac4"),
    uninit_fill=_c("#f1f1f1"), uninit_text=_c("#9aa0a6"),
    changed_fill=_c("#fff1a8"), changed_border=_c("#e0a800"),
    read_fill=_c("#dcebff"), read_border=_c("#5b8fd6"),
    problem_fill=_c("#fde7e5"), problem_border=_c("#d93025"),
    arrow=_c("#1f6fd1"), arrow_bad=_c("#d93025"), null_text=_c("#9aa0a6"),
    next_line=_c("#d4f5d4"), prev_line=_c("#eeeeee"), error_line=_c("#fbd3cf"), warning_line=_c("#fff0c2"),
    breakpoint=_c("#d93025"), gutter=_c("#f3f3f3"), gutter_text=_c("#9aa0a6"),
    syn_keyword=_c("#7b30d0"), syn_type=_c("#0b7d8c"), syn_string=_c("#1c7d23"), syn_number=_c("#b5530b"),
    syn_comment=_c("#8c8c8c"), syn_preproc=_c("#b0207e"), syn_function=_c("#1f5fbf"),
    out_stderr=_c("#c5221f"), out_system=_c("#80868b"),
    tb_button=_c("#ffffff"), tb_border=_c("#aab1bb"), tb_hover=_c("#e3edfc"), tb_checked=_c("#cfe0fb"), tb_accent=_c("#2f6fd0"),
)

DARK = Theme(
    dark=True,
    canvas=_c("#1e1f22"), text=_c("#e8eaed"), text_dim=_c("#a0a4ab"), address=_c("#80868b"),
    frame_fill=_c("#1f2939"), frame_border=_c("#3d5b88"), frame_header=_c("#273650"), frame_top_border=_c("#6ea8fe"),
    heap_fill=_c("#1d3125"), heap_border=_c("#3f7d52"), heap_header=_c("#24402e"),
    freed_fill=_c("#2a2a2a"), freed_border=_c("#5a5a5a"),
    leak_fill=_c("#3a2a17"), leak_border=_c("#f29d49"),
    static_fill=_c("#302d1d"), static_border=_c("#8a7a3d"), static_header=_c("#3d3922"),
    box_fill=_c("#2b2d31"), box_border=_c("#555a63"),
    uninit_fill=_c("#26272a"), uninit_text=_c("#7c8087"),
    changed_fill=_c("#5a4c10"), changed_border=_c("#e3b341"),
    read_fill=_c("#1e3554"), read_border=_c("#6ea8fe"),
    problem_fill=_c("#4a1f1d"), problem_border=_c("#f28b82"),
    arrow=_c("#8ab4f8"), arrow_bad=_c("#f28b82"), null_text=_c("#80868b"),
    next_line=_c("#1f3d24"), prev_line=_c("#2c2d30"), error_line=_c("#4a1f1d"), warning_line=_c("#3d3417"),
    breakpoint=_c("#f28b82"), gutter=_c("#232427"), gutter_text=_c("#6f737a"),
    syn_keyword=_c("#c792ea"), syn_type=_c("#56c4d3"), syn_string=_c("#a5d6a7"), syn_number=_c("#f7a96b"),
    syn_comment=_c("#7f848e"), syn_preproc=_c("#f48fb1"), syn_function=_c("#82aaff"),
    out_stderr=_c("#f28b82"), out_system=_c("#9aa0a6"),
    tb_button=_c("#3a3d42"), tb_border=_c("#62676f"), tb_hover=_c("#48505c"), tb_checked=_c("#2d4a78"), tb_accent=_c("#6ea8fe"),
)

THEMES = {"dark": DARK, "light": LIGHT}
DEFAULT_THEME = "dark"


def _dark_palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#2b2d30",
        QPalette.ColorRole.WindowText: "#e8eaed",
        QPalette.ColorRole.Base: "#1e1f22",
        QPalette.ColorRole.AlternateBase: "#26282b",
        QPalette.ColorRole.ToolTipBase: "#2b2d30",
        QPalette.ColorRole.ToolTipText: "#e8eaed",
        QPalette.ColorRole.PlaceholderText: "#80868b",
        QPalette.ColorRole.Text: "#e8eaed",
        QPalette.ColorRole.Button: "#3a3d42",
        QPalette.ColorRole.ButtonText: "#e8eaed",
        QPalette.ColorRole.BrightText: "#ff6b6b",
        QPalette.ColorRole.Light: "#4a4e55",
        QPalette.ColorRole.Midlight: "#3f4247",
        QPalette.ColorRole.Mid: "#303236",
        QPalette.ColorRole.Dark: "#1a1b1d",
        QPalette.ColorRole.Shadow: "#000000",
        QPalette.ColorRole.Highlight: "#2f6fd0",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: "#8ab4f8",
        QPalette.ColorRole.LinkVisited: "#c58af9",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#6f737a"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#252629"))
    return p


def apply_app_theme(name: str) -> Theme:
    """Switch the whole application to the "dark" or "light" theme.

    The Fusion style is used for both, so that the app looks the same on every
    system and follows our colours instead of the system setting.
    """
    app = QApplication.instance()
    theme = THEMES.get(name, THEMES[DEFAULT_THEME])
    if app is None:
        return theme
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    app.setPalette(_dark_palette() if theme.dark else app.style().standardPalette())
    return theme


def toolbar_stylesheet(t: Theme) -> str:
    """Toolbar buttons with a visible outline, so it is clear where each one starts and ends."""
    return f"""
QToolBar#runbar {{ spacing: 4px; padding: 4px 6px; border: none; }}
QToolBar#runbar::separator {{ width: 1px; background: {t.tb_border.name()}; margin: 4px 6px; }}
QToolBar#runbar QToolButton {{
    background: {t.tb_button.name()};
    color: {t.text.name()};
    border: 1px solid {t.tb_border.name()};
    border-radius: 5px;
    padding: 3px 8px;
}}
QToolBar#runbar QToolButton:hover {{ background: {t.tb_hover.name()}; border-color: {t.tb_accent.name()}; }}
QToolBar#runbar QToolButton:pressed {{ background: {t.tb_checked.name()}; }}
QToolBar#runbar QToolButton:checked {{ background: {t.tb_checked.name()}; border-color: {t.tb_accent.name()}; }}
QToolBar#runbar QToolButton:disabled {{ background: transparent; color: {t.text_dim.name()}; border-color: {t.tb_border.name()}; }}
QToolBar#runbar QToolButton#executeButton {{ font-weight: bold; border: 1px solid {t.problem_border.name()}; }}
QToolBar#runbar QToolButton#executeButton:hover {{ background: {t.problem_fill.name()}; }}
QToolBar#runbar QComboBox {{ padding: 2px 6px; }}
"""


def current_theme() -> Theme:
    app = QGuiApplication.instance()
    if app is None:
        return LIGHT
    window = app.palette().color(QPalette.ColorRole.Window)
    return DARK if window.lightness() < 128 else LIGHT


def mono_font(size: float = 10.0) -> QFont:
    for family in ("Cascadia Mono", "Consolas", "JetBrains Mono", "Menlo", "DejaVu Sans Mono"):
        if family in QFontDatabase.families():
            f = QFont(family)
            f.setPointSizeF(size)
            f.setStyleHint(QFont.StyleHint.Monospace)
            return f
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSizeF(size)
    return f


def ui_font(size: float = 9.5, bold: bool = False) -> QFont:
    f = QFont(QGuiApplication.font())
    f.setPointSizeF(size)
    f.setBold(bold)
    return f

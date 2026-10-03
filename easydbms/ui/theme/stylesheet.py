"""Builds the Qt stylesheet from design tokens."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from .assets import check_icon_path, close_icon_path
from .palette import SYNTAX, THEMES, SyntaxColors, Tokens


def build_stylesheet(t: Tokens) -> str:
    return f"""
* {{ font-size: 13px; }}
QWidget {{ background-color: {t.bg}; color: {t.text}; }}
QMainWindow, QDialog {{ background-color: {t.bg}; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {t.text_muted}; }}
QLabel[error="true"] {{ color: {t.danger}; }}
QLabel[heading="true"] {{ font-size: 15px; font-weight: 600; }}
QToolTip {{ background-color: {t.panel_alt}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}

QFrame[panel="true"], QWidget[panel="true"] {{ background-color: {t.panel}; }}
QFrame[card="true"] {{ background-color: {t.panel}; border: 1px solid {t.border}; border-radius: 8px; }}
QFrame[card="true"] QLabel {{ background: transparent; }}

QFrame#completionPopup {{ background-color: {t.panel}; border: 1px solid {t.border}; border-radius: 6px; }}
QFrame#completionPopup QListView {{ background-color: {t.panel}; border: none; outline: 0; }}
QLabel#completionDoc {{ background-color: {t.panel_alt}; color: {t.text}; border-left: 1px solid {t.border}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
    background-color: {t.input_bg}; color: {t.text};
    border: 1px solid {t.border}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {t.selection}; selection-color: {t.text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {t.accent}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {t.danger}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {t.text_muted}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {t.panel}; border: 1px solid {t.border};
    selection-background-color: {t.selection}; outline: 0;
}}

QPushButton, QToolButton {{
    background-color: {t.panel_alt}; color: {t.text};
    border: 1px solid {t.border}; border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover, QToolButton:hover {{ background-color: {t.hover}; border-color: {t.text_muted}; }}
QPushButton:pressed, QToolButton:pressed {{ background-color: {t.border}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t.text_muted}; border-color: {t.border}; }}
QPushButton[primary="true"] {{ background-color: {t.accent}; color: {t.accent_text}; border-color: {t.accent}; }}
QPushButton[primary="true"]:hover {{ background-color: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton[primary="true"]:disabled {{ background-color: {t.panel_alt}; color: {t.text_muted}; border-color: {t.border}; }}
QPushButton[flat="true"], QToolButton[flat="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[danger="true"]:hover {{ color: {t.danger}; border-color: {t.danger}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton[compact="true"] {{ padding: 0px; font-size: 15px; font-weight: 600; }}
QToolButton[switcher="true"] {{ padding: 6px 12px; font-weight: 600; }}
QToolButton[switcher="true"][production="true"] {{ border-color: {t.danger}; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid {t.border}; border-radius: 4px; background: {t.input_bg}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({check_icon_path("#ffffff")}); }}
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {t.border}; border-radius: 8px; background: {t.input_bg}; }}
QRadioButton::indicator:checked {{ border: 1px solid {t.accent}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {t.accent}, stop:0.45 {t.accent}, stop:0.55 {t.input_bg}, stop:1 {t.input_bg}); }}

QListWidget {{ background-color: {t.panel}; border: 1px solid {t.border}; border-radius: 6px; outline: 0; padding: 4px; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 5px; }}
QListWidget::item:hover {{ background-color: {t.hover}; }}
QListWidget::item:selected {{ background-color: {t.selection}; color: {t.text}; }}

QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 6px; top: -1px; background: {t.panel}; }}
QTabBar::tab {{ background: transparent; color: {t.text_muted}; padding: 7px 16px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabBar::close-button {{ image: url({close_icon_path(t.text_muted)}); subcontrol-position: right; margin: 2px; border-radius: 3px; }}
QTabBar::close-button:hover {{ image: url({close_icon_path(t.text)}); background: {t.hover}; }}

QTableView {{ background-color: {t.panel}; alternate-background-color: {t.panel_alt}; gridline-color: {t.border}; color: {t.text}; selection-background-color: {t.selection}; selection-color: {t.text}; border: none; }}
QTableView::item {{ padding: 0 6px; }}
QHeaderView::section {{ background-color: {t.panel_alt}; color: {t.text_muted}; border: none; border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border}; padding: 5px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background-color: {t.panel_alt}; border: none; }}

QMenuBar {{ background-color: {t.panel}; }}
QMenuBar::item:selected {{ background-color: {t.hover}; }}
QMenu {{ background-color: {t.panel}; border: 1px solid {t.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {t.selection}; }}
QMenu::item:disabled {{ color: {t.text_muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}

QStatusBar {{ background-color: {t.panel}; border-top: 1px solid {t.border}; }}
QStatusBar QLabel {{ padding: 0 6px; }}
QToolBar {{ background-color: {t.panel}; border: none; border-bottom: 1px solid {t.border}; spacing: 8px; padding: 6px 8px; }}
QSplitter::handle {{ background-color: {t.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {t.text_muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMessageBox {{ background-color: {t.bg}; }}
QProgressBar {{ background: {t.input_bg}; border: 1px solid {t.border}; border-radius: 4px; height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
"""


THEME_PROPERTY = "erdTheme"


def apply_theme(app: QApplication, name: str) -> None:
    app.setProperty(THEME_PROPERTY, name)
    app.setStyleSheet(build_stylesheet(THEMES[name]))


def current_tokens() -> Tokens:
    """Tokens of the theme applied to the running application (dark when none was applied)."""
    app = QApplication.instance()
    name = app.property(THEME_PROPERTY) if app is not None else None
    return THEMES.get(str(name), THEMES["dark"])


def current_syntax() -> SyntaxColors:
    return SYNTAX[current_tokens().name]

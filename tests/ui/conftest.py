"""Shared fixtures for GUI tests (Qt runs on the ``offscreen`` platform, see tests/conftest.py)."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QWidget,
)

from easydbms.core.connections import FileConnection, MemorySecretStore, ServerConnection
from easydbms.core.paths import AppPaths
from easydbms.core.services import Services, build_services
from easydbms.ui.i18n import set_language
from easydbms.ui.results.dialogs import KeyDialog, PreviewDialog, TextDialog
from easydbms.ui.runtime import BackgroundRunner, EventBridge
from easydbms.ui.session_panel import SessionPanel
from easydbms.ui.theme import apply_theme, current_tokens
from easydbms.ui.workspace import QueryWorkspace


@dataclass
class Env:
    services: Services
    bridge: EventBridge
    runner: BackgroundRunner
    secrets: MemorySecretStore
    tmp_path: Path

    def sqlite_file(self, name: str = "data.db") -> Path:
        path = self.tmp_path / name
        sqlite3.connect(path).close()
        return path

    def add_sqlite(self, name: str = "lite", **extra: Any) -> FileConnection:
        config = FileConnection.model_validate(
            {"name": name, "path": str(self.sqlite_file(f"{name}.db")), **extra}
        )
        self.services.store.save(config)
        return config

    def add_server(self, name: str = "srv", **extra: Any) -> ServerConnection:
        data = {"name": name, "dialect": "postgresql", "host": "db.invalid", "user": "u"} | extra
        config = ServerConnection.model_validate(data)
        self.services.store.save(config)
        return config


@pytest.fixture(autouse=True)
def _english_ui() -> Iterator[None]:
    set_language("en")
    yield
    set_language("en")


@pytest.fixture
def env(qapp: QApplication, tmp_path: Path) -> Iterator[Env]:
    if current_tokens().name != "dark":  # re-applying a stylesheet re-polishes every live widget
        apply_theme(qapp, "dark")
    bridge = EventBridge()
    secrets = MemorySecretStore()
    services = build_services(
        AppPaths.under(tmp_path), listener=bridge.post, secrets=secrets, environ={}
    )
    runner = BackgroundRunner()
    yield Env(services, bridge, runner, secrets, tmp_path)
    for widget in QApplication.allWidgets():  # a debounce timer must not outlive app.db
        if isinstance(widget, QueryWorkspace):
            # RuntimeError: the C++ object is gone; ProgrammingError: its app.db was closed
            with contextlib.suppress(RuntimeError, sqlite3.ProgrammingError):
                widget.shutdown()
    runner.shutdown()
    services.close()


@dataclass
class Prompts:
    """Replaces the blocking modal dialogs: records them and answers from a script."""

    question_answer: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes
    questions: list[str] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)
    text_answers: list[tuple[str, bool]] = field(default_factory=list)
    text_prompts: list[str] = field(default_factory=list)
    open_file: str = ""
    save_file: str = ""
    #: The review dialog of edited rows: answer it, and keep the ones that were shown.
    preview_accepts: bool = True
    previews: list[PreviewDialog] = field(default_factory=list)
    #: Columns ticked in the key dialog (``None`` cancels it) and the dialogs shown.
    key_choice: tuple[str, ...] | None = None
    key_dialogs: list[KeyDialog] = field(default_factory=list)
    #: Text typed into the long-text dialog (``None`` cancels it).
    long_text: str | None = None


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> Prompts:
    state = Prompts()

    def question(parent: Any, title: str, text: str, *args: Any, **kwargs: Any) -> Any:
        state.questions.append(f"{title}|{text}")
        return state.question_answer

    def message(parent: Any, title: str, text: str, *args: Any, **kwargs: Any) -> Any:
        state.messages.append((title, text))
        return QMessageBox.StandardButton.Ok

    def get_text(
        parent: Any, title: str, label: str, *args: Any, **kwargs: Any
    ) -> tuple[str, bool]:
        state.text_prompts.append(label)
        return state.text_answers.pop(0) if state.text_answers else ("", False)

    def preview_exec(dialog: PreviewDialog) -> int:
        state.previews.append(dialog)
        return QDialog.DialogCode.Accepted if state.preview_accepts else QDialog.DialogCode.Rejected

    def key_exec(dialog: KeyDialog) -> int:
        state.key_dialogs.append(dialog)
        if state.key_choice is None:
            return QDialog.DialogCode.Rejected
        for row in range(dialog.list.count()):
            item = dialog.list.item(row)
            wanted = item.data(Qt.ItemDataRole.UserRole) in state.key_choice
            item.setCheckState(Qt.CheckState.Checked if wanted else Qt.CheckState.Unchecked)
        return QDialog.DialogCode.Accepted

    def text_exec(dialog: TextDialog) -> int:
        if state.long_text is None:
            return QDialog.DialogCode.Rejected
        dialog.editor.setPlainText(state.long_text)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PreviewDialog, "exec", preview_exec)
    monkeypatch.setattr(KeyDialog, "exec", key_exec)
    monkeypatch.setattr(TextDialog, "exec", text_exec)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(message))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(message))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(get_text))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (state.open_file, ""))
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (state.save_file, ""))
    )
    return state


WaitUntil = Callable[..., None]


def shown_texts(panel: SessionPanel) -> str:
    """All visible label texts of the page the session panel currently shows."""
    page = panel._stack.currentWidget()
    assert isinstance(page, QWidget)
    return " | ".join(
        label.text() for label in page.findChildren(QLabel) if label.isVisibleTo(page)
    )


SHOP_SCRIPT = """
CREATE TABLE author (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE);
CREATE TABLE book (id INTEGER PRIMARY KEY, author_id INTEGER NOT NULL REFERENCES author(id),
                   title TEXT, published INTEGER);
CREATE TABLE tag (id INTEGER PRIMARY KEY, label TEXT);
CREATE TABLE book_tag (book_id INTEGER NOT NULL REFERENCES book(id),
                       tag_id INTEGER NOT NULL REFERENCES tag(id), PRIMARY KEY (book_id, tag_id));
CREATE TABLE lonely (id INTEGER PRIMARY KEY, note TEXT);
CREATE VIEW author_names AS SELECT id, name FROM author;
"""


def add_shop(env: Env, name: str = "Shop", rows: int = 0) -> FileConnection:
    """A SQLite connection whose file holds a small bookshop schema (and ``rows`` books)."""
    config = env.add_sqlite(name)
    con = sqlite3.connect(config.path)
    con.executescript(SHOP_SCRIPT)
    con.execute(
        "INSERT INTO author (id, name, email) VALUES (1, 'Ann', 'ann@x'), (2, 'Bob', 'b@x')"
    )
    for i in range(1, rows + 1):
        con.execute(
            "INSERT INTO book (id, author_id, title, published) VALUES (?, ?, ?, ?)",
            (i, 1 + i % 2, f"Book {i:03d}", 1990 + i % 30),
        )
    con.commit()
    con.close()
    return config

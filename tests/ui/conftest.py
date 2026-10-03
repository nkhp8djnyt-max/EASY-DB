"""Shared fixtures for GUI tests (Qt runs on the ``offscreen`` platform, see tests/conftest.py)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QLabel, QMessageBox, QWidget

from easydbms.core.connections import FileConnection, MemorySecretStore, ServerConnection
from easydbms.core.paths import AppPaths
from easydbms.core.services import Services, build_services
from easydbms.ui.i18n import set_language
from easydbms.ui.runtime import BackgroundRunner, EventBridge
from easydbms.ui.session_panel import SessionPanel
from easydbms.ui.theme import apply_theme, current_tokens


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

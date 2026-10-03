"""Query history and saved queries: browse, search, reopen, save."""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.queries import (
    HistoryEntry,
    HistoryOutcome,
    HistoryStore,
    SavedQuery,
    SavedQueryStore,
)
from .i18n import tr
from .theme import current_tokens

_ROLE_ID = Qt.ItemDataRole.UserRole
_HISTORY_PAGE = 500


def format_when(moment: float, now: float | None = None) -> str:
    """ "just now", "5 min ago", "today 14:03", "yesterday 09:10", "2024-02-29 13:45"."""
    current = time.time() if now is None else now
    seconds = current - moment
    then = datetime.fromtimestamp(moment)
    today = datetime.fromtimestamp(current).date()
    if seconds < 45:
        return tr("just now")
    if seconds < 3600:
        return tr("{n} min ago", n=max(1, round(seconds / 60)))
    if then.date() == today:
        return tr("today {time}", time=f"{then:%H:%M}")
    if (today - then.date()).days == 1:
        return tr("yesterday {time}", time=f"{then:%H:%M}")
    return f"{then:%Y-%m-%d %H:%M}"


def format_duration(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else f"{seconds:.1f} s"


class SaveQueryDialog(QDialog):
    """Name (and optionally a folder) for a query that is about to be saved."""

    def __init__(
        self,
        sql: str,
        folders: list[str],
        name: str = "",
        folder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Save query"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.addWidget(self._muted(tr("Name")))
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText(tr("Monthly revenue"))
        layout.addWidget(self.name_edit)
        layout.addWidget(self._muted(tr("Folder (optional)")))
        self.folder_combo = QComboBox()
        self.folder_combo.setEditable(True)
        self.folder_combo.addItems(folders)
        self.folder_combo.setEditText(folder)
        self.folder_combo.lineEdit().setPlaceholderText(tr("No folder"))  # type: ignore[union-attr]
        layout.addWidget(self.folder_combo)
        self.scope_check = QCheckBox(tr("Only for this connection"))
        layout.addWidget(self.scope_check)
        preview = QPlainTextEdit(sql)
        preview.setReadOnly(True)
        preview.setMaximumHeight(130)
        layout.addWidget(preview)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.name_edit.returnPressed.connect(self._accept)

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("muted", True)
        return label

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def folder(self) -> str:
        return self.folder_combo.currentText().strip()

    @property
    def only_this_connection(self) -> bool:
        return self.scope_check.isChecked()

    def _accept(self) -> None:
        if self.name:
            self.accept()
        else:
            self.name_edit.setFocus()


class LibraryDialog(QDialog):
    """A window with two lists: what was run, and what was kept."""

    #: ``(sql, title, dialect id)``: open the statement in a new query tab.
    openRequested = Signal(str, str, str)
    #: ``sql``: put the statement at the cursor of the current tab.
    insertRequested = Signal(str)

    def __init__(
        self,
        history: HistoryStore,
        saved: SavedQueryStore,
        connection_id: str,
        connection_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._history = history
        self._saved = saved
        self._connection_id = connection_id
        self.setWindowTitle(tr("History and saved queries — {name}", name=connection_name))
        self.setMinimumSize(820, 560)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_history(), tr("History"))
        self.tabs.addTab(self._build_saved(), tr("Saved queries"))
        layout.addWidget(self.tabs)
        self.refresh()

    # ------------------------------------------------------------------ history tab

    def _build_history(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("Search the history…"))
        self.search_edit.setClearButtonEnabled(True)
        self.errors_check = QCheckBox(tr("Only failures"))
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.errors_check)
        layout.addLayout(top)
        self.history_tree = QTreeWidget()
        self.history_tree.setRootIsDecorated(False)
        self.history_tree.setUniformRowHeights(True)
        self.history_tree.setHeaderLabels([tr("When"), tr("Statement"), tr("Time"), tr("Result")])
        self.history_tree.setColumnWidth(0, 130)
        self.history_tree.setColumnWidth(1, 440)
        self.history_tree.setColumnWidth(2, 70)
        layout.addWidget(self.history_tree, 1)
        self.history_preview = QPlainTextEdit()
        self.history_preview.setReadOnly(True)
        self.history_preview.setMaximumHeight(140)
        layout.addWidget(self.history_preview)
        self.history_message = QLabel()
        self.history_message.setProperty("error", True)
        self.history_message.setWordWrap(True)
        self.history_message.hide()
        layout.addWidget(self.history_message)
        buttons = QHBoxLayout()
        self.history_open = QPushButton(tr("Open in a new tab"))
        self.history_open.setProperty("primary", True)
        self.history_insert = QPushButton(tr("Insert at the cursor"))
        self.history_copy = QPushButton(tr("Copy"))
        self.history_save = QPushButton(tr("Save as a query…"))
        self.history_delete = QPushButton(tr("Delete"))
        self.history_clear = QPushButton(tr("Clear history…"))
        self.history_clear.setProperty("danger", True)
        for button in (
            self.history_open,
            self.history_insert,
            self.history_copy,
            self.history_save,
            self.history_delete,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.history_clear)
        layout.addLayout(buttons)

        self.search_edit.textChanged.connect(lambda _text: self.refresh_history())
        self.errors_check.toggled.connect(lambda _checked: self.refresh_history())
        self.history_tree.currentItemChanged.connect(lambda *_: self._on_history_selected())
        self.history_tree.itemDoubleClicked.connect(lambda *_: self._open_history())
        self.history_open.clicked.connect(self._open_history)
        self.history_insert.clicked.connect(self._insert_history)
        self.history_copy.clicked.connect(self._copy_history)
        self.history_save.clicked.connect(self._save_history)
        self.history_delete.clicked.connect(self._delete_history)
        self.history_clear.clicked.connect(self._clear_history)
        return page

    def refresh_history(self) -> None:
        selected = self._selected_history_id()
        entries = self._history.recent(
            self._connection_id,
            search=self.search_edit.text(),
            errors_only=self.errors_check.isChecked(),
            limit=_HISTORY_PAGE,
        )
        tokens = current_tokens()
        self.history_tree.clear()
        for entry in entries:
            item = QTreeWidgetItem(self.history_tree)
            item.setData(0, _ROLE_ID, entry.id)
            item.setText(0, format_when(entry.executed_at))
            item.setText(1, entry.first_line + (f"   x{entry.runs}" if entry.runs > 1 else ""))
            item.setText(2, format_duration(entry.duration))
            item.setText(3, self._result_text(entry))
            item.setToolTip(1, entry.sql)
            if entry.outcome is HistoryOutcome.ERROR:
                item.setForeground(3, Qt.GlobalColor.red)
                item.setToolTip(3, entry.error)
            elif entry.outcome is HistoryOutcome.CANCELLED:
                item.setForeground(3, Qt.GlobalColor.darkYellow)
            else:
                item.setForeground(3, QColor(tokens.success))
            if entry.id == selected:
                self.history_tree.setCurrentItem(item)
        first = self.history_tree.topLevelItem(0)
        if self.history_tree.currentItem() is None and first is not None:
            self.history_tree.setCurrentItem(first)
        self._on_history_selected()

    @staticmethod
    def _result_text(entry: HistoryEntry) -> str:
        if entry.outcome is HistoryOutcome.ERROR:
            return tr("failed")
        if entry.outcome is HistoryOutcome.CANCELLED:
            return tr("cancelled")
        if entry.row_count is None:
            return tr("done")
        return tr("{count} rows", count=entry.row_count)

    def _selected_history_id(self) -> int | None:
        item = self.history_tree.currentItem()
        return int(item.data(0, _ROLE_ID)) if item is not None else None

    def selected_history(self) -> HistoryEntry | None:
        entry_id = self._selected_history_id()
        if entry_id is None:
            return None
        for entry in self._history.recent(self._connection_id, limit=_HISTORY_PAGE):
            if entry.id == entry_id:
                return entry
        return None

    def _on_history_selected(self) -> None:
        entry = self.selected_history()
        self.history_preview.setPlainText(entry.sql if entry else "")
        if entry is not None and entry.error:
            self.history_message.setText(entry.error)
            self.history_message.show()
        else:
            self.history_message.hide()
        for button in (
            self.history_open,
            self.history_insert,
            self.history_copy,
            self.history_save,
            self.history_delete,
        ):
            button.setEnabled(entry is not None)
        self.history_clear.setEnabled(self.history_tree.topLevelItemCount() > 0)

    def _open_history(self) -> None:
        entry = self.selected_history()
        if entry is not None:
            self.openRequested.emit(entry.sql, entry.first_line[:40], entry.dialect)

    def _insert_history(self) -> None:
        entry = self.selected_history()
        if entry is not None:
            self.insertRequested.emit(entry.sql)

    def _copy_history(self) -> None:
        entry = self.selected_history()
        if entry is not None:
            QGuiApplication.clipboard().setText(entry.sql)

    def _save_history(self) -> None:
        entry = self.selected_history()
        if entry is not None:
            self.save_query(entry.sql, entry.dialect, name=entry.first_line[:60])

    def _delete_history(self) -> None:
        entry_id = self._selected_history_id()
        if entry_id is not None:
            self._history.delete(entry_id)
            self.refresh_history()

    def _clear_history(self) -> None:
        answer = QMessageBox.question(
            self,
            tr("Clear history"),
            tr("Delete the whole history of this connection? Saved queries are kept."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._history.clear(self._connection_id)
            self.refresh_history()

    # ------------------------------------------------------------------ saved tab

    def _build_saved(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.saved_search = QLineEdit()
        self.saved_search.setPlaceholderText(tr("Search saved queries…"))
        self.saved_search.setClearButtonEnabled(True)
        layout.addWidget(self.saved_search)
        self.saved_tree = QTreeWidget()
        self.saved_tree.setHeaderLabels([tr("Name"), tr("Dialect"), tr("Changed")])
        self.saved_tree.setColumnWidth(0, 360)
        self.saved_tree.setColumnWidth(1, 100)
        layout.addWidget(self.saved_tree, 1)
        self.saved_preview = QPlainTextEdit()
        self.saved_preview.setReadOnly(True)
        self.saved_preview.setMaximumHeight(160)
        layout.addWidget(self.saved_preview)
        buttons = QHBoxLayout()
        self.saved_open = QPushButton(tr("Open in a new tab"))
        self.saved_open.setProperty("primary", True)
        self.saved_insert = QPushButton(tr("Insert at the cursor"))
        self.saved_rename = QPushButton(tr("Rename…"))
        self.saved_move = QPushButton(tr("Move to a folder…"))
        self.saved_delete = QPushButton(tr("Delete"))
        self.saved_delete.setProperty("danger", True)
        for button in (
            self.saved_open,
            self.saved_insert,
            self.saved_rename,
            self.saved_move,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.saved_delete)
        layout.addLayout(buttons)

        self.saved_search.textChanged.connect(lambda _text: self.refresh_saved())
        self.saved_tree.currentItemChanged.connect(lambda *_: self._on_saved_selected())
        self.saved_tree.itemDoubleClicked.connect(lambda *_: self._open_saved())
        self.saved_open.clicked.connect(self._open_saved)
        self.saved_insert.clicked.connect(self._insert_saved)
        self.saved_rename.clicked.connect(self._rename_saved)
        self.saved_move.clicked.connect(self._move_saved)
        self.saved_delete.clicked.connect(self._delete_saved)
        return page

    def refresh_saved(self) -> None:
        selected = self._selected_saved_id()
        needle = self.saved_search.text().strip().casefold()
        queries = [
            q
            for q in self._saved.all(self._connection_id)
            if not needle or needle in q.name.casefold() or needle in q.sql.casefold()
        ]
        self.saved_tree.clear()
        folders: dict[str, QTreeWidgetItem] = {}
        for query in queries:
            parent: QTreeWidgetItem | None = None
            if query.folder:
                parent = folders.get(query.folder)
                if parent is None:
                    parent = QTreeWidgetItem(self.saved_tree, [query.folder])
                    parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    folders[query.folder] = parent
            item = (
                QTreeWidgetItem(parent) if parent is not None else QTreeWidgetItem(self.saved_tree)
            )
            item.setData(0, _ROLE_ID, query.id)
            item.setText(0, query.name)
            item.setText(1, query.dialect)
            item.setText(2, format_when(query.updated_at))
            item.setToolTip(0, query.sql)
            if query.id == selected:
                self.saved_tree.setCurrentItem(item)
        self.saved_tree.expandAll()
        if self.saved_tree.currentItem() is None:
            first = self._first_query_item()
            if first is not None:
                self.saved_tree.setCurrentItem(first)
        self._on_saved_selected()

    def _first_query_item(self) -> QTreeWidgetItem | None:
        stack = [
            self.saved_tree.topLevelItem(i) for i in range(self.saved_tree.topLevelItemCount())
        ]
        for item in stack:
            if item is None:
                continue
            if item.data(0, _ROLE_ID) is not None:
                return item
            for child_index in range(item.childCount()):
                child = item.child(child_index)
                if child is not None and child.data(0, _ROLE_ID) is not None:
                    return child
        return None

    def _selected_saved_id(self) -> int | None:
        item = self.saved_tree.currentItem()
        value = item.data(0, _ROLE_ID) if item is not None else None
        return int(value) if value is not None else None

    def selected_saved(self) -> SavedQuery | None:
        query_id = self._selected_saved_id()
        return self._saved.get(query_id) if query_id is not None else None

    def _on_saved_selected(self) -> None:
        query = self.selected_saved()
        self.saved_preview.setPlainText(query.sql if query else "")
        for button in (
            self.saved_open,
            self.saved_insert,
            self.saved_rename,
            self.saved_move,
            self.saved_delete,
        ):
            button.setEnabled(query is not None)

    def _open_saved(self) -> None:
        query = self.selected_saved()
        if query is not None:
            self.openRequested.emit(query.sql, query.name, query.dialect)

    def _insert_saved(self) -> None:
        query = self.selected_saved()
        if query is not None:
            self.insertRequested.emit(query.sql)

    def _rename_saved(self) -> None:
        query = self.selected_saved()
        if query is None:
            return
        name, accepted = QInputDialog.getText(
            self, tr("Rename query"), tr("Name:"), text=query.name
        )
        if accepted and name.strip():
            self._saved.update(query.id, name=name)
            self.refresh_saved()

    def _move_saved(self) -> None:
        query = self.selected_saved()
        if query is None:
            return
        folder, accepted = QInputDialog.getText(
            self, tr("Move to a folder"), tr("Folder (empty: none):"), text=query.folder
        )
        if accepted:
            self._saved.update(query.id, folder=folder)
            self.refresh_saved()

    def _delete_saved(self) -> None:
        query = self.selected_saved()
        if query is None:
            return
        answer = QMessageBox.question(
            self,
            tr("Delete query"),
            tr("Delete the saved query “{name}”?", name=query.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._saved.delete(query.id)
            self.refresh_saved()

    # ------------------------------------------------------------------ shared

    def save_query(self, sql: str, dialect: str, *, name: str = "") -> SavedQuery | None:
        """Ask for a name and keep ``sql``; ``None`` if the person cancelled."""
        dialog = SaveQueryDialog(sql, self._saved.folders(self._connection_id), name, parent=self)
        if not dialog.exec():
            return None
        saved = self._saved.add(
            dialog.name,
            sql,
            dialect,
            folder=dialog.folder,
            connection_id=self._connection_id if dialog.only_this_connection else "",
        )
        self.refresh_saved()
        return saved

    def refresh(self) -> None:
        self.refresh_history()
        self.refresh_saved()

    def show_tab(self, which: str) -> None:
        self.tabs.setCurrentIndex(1 if which == "saved" else 0)
        self.show()
        self.raise_()
        self.activateWindow()

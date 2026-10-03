"""The "Airbnb ▾" drop-down above the ERD pane: every saved connection, one click to switch."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

from ..core.connections import ConnectionStore, FileConnection, ServerConnection
from ..core.session import ConnectionManager, SessionState
from .i18n import tr
from .icons import dot_icon
from .theme import color_hex, current_tokens


class DbSwitcher(QToolButton):
    #: The user picked a connection in the menu.
    connectionChosen = Signal(str)
    #: The user asked to manage the list of connections.
    manageRequested = Signal()

    def __init__(
        self, store: ConnectionStore, manager: ConnectionManager, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._manager = manager
        self.setProperty("switcher", True)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self.setMenu(self._menu)
        self.refresh()

    def refresh(self) -> None:
        """Update the button text and icon from the manager's active connection."""
        active = self._manager.active
        if active is None:
            self.setText(tr("Select a connection") + "  ▾")
            self.setIcon(dot_icon(current_tokens().text_muted, ring=True))
            self.setProperty("production", False)
            self.setToolTip(tr("Choose the database to work with"))
        else:
            self.setText(active.config.name + "  ▾")
            self.setIcon(dot_icon(self._state_color(active.state)))
            self.setProperty("production", active.config.production)
            self.setToolTip(f"{active.config.subtitle}\n{self._state_text(active.state)}")
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _state_color(state: SessionState) -> str:
        tokens = current_tokens()
        return {
            SessionState.READY: tokens.success,
            SessionState.CONNECTING: tokens.warning,
            SessionState.ERROR: tokens.danger,
            SessionState.DISCONNECTED: tokens.text_muted,
        }[state]

    @staticmethod
    def _state_text(state: SessionState) -> str:
        return {
            SessionState.READY: tr("Connected"),
            SessionState.CONNECTING: tr("Connecting…"),
            SessionState.ERROR: tr("Connection failed"),
            SessionState.DISCONNECTED: tr("Not connected"),
        }[state]

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        configs = sorted(
            self._store.all(), key=lambda c: (c.group == "", c.group.lower(), c.name.lower())
        )
        group_actions = QActionGroup(self._menu)
        group_actions.setExclusive(True)
        last_group: str | None = None
        multiple_groups = len({c.group for c in configs}) > 1
        for config in configs:
            if multiple_groups and config.group != last_group:
                header = QAction((config.group or tr("No group")).upper(), self._menu)
                header.setEnabled(False)
                self._menu.addAction(header)
            last_group = config.group
            self._menu.addAction(self._action_for(config, group_actions))
        if not configs:
            empty = QAction(tr("No saved connections"), self._menu)
            empty.setEnabled(False)
            self._menu.addAction(empty)
        self._menu.addSeparator()
        manage = QAction(tr("Manage connections…"), self._menu)
        manage.triggered.connect(self.manageRequested)
        self._menu.addAction(manage)

    def _action_for(
        self, config: ServerConnection | FileConnection, group: QActionGroup
    ) -> QAction:
        state = self._manager.state_of(config.id)
        connected = state is SessionState.READY
        action = QAction(config.name, self._menu)
        action.setIcon(dot_icon(color_hex(config.display_color), ring=not connected))
        action.setCheckable(True)
        action.setChecked(config.id == self._manager.active_id)
        action.setToolTip(f"{config.subtitle} — {self._state_text(state)}")
        group.addAction(action)
        connection_id = config.id
        action.triggered.connect(lambda _checked=False: self.connectionChosen.emit(connection_id))
        return action

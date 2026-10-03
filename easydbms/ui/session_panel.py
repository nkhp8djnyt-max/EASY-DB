"""Left pane for stage 1: what is going on with the active connection."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, SignalInstance
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.session import Session, SessionState
from .failure_hints import hint_for
from .i18n import tr

_FLAVORS = {"postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "sqlite": "SQLite"}


class SessionPanel(QWidget):
    manageRequested = Signal()
    retryRequested = Signal(str)
    editRequested = Signal(str)
    disconnectRequested = Signal(str)
    cancelRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._connection_id: str | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        self._stack = QStackedWidget()
        outer.addStretch(1)
        outer.addWidget(self._stack, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)
        self._empty = self._page_empty()
        self._connecting = self._page_connecting()
        self._error = self._page_error()
        self._ready = self._page_ready()
        for page in (self._empty, self._connecting, self._error, self._ready):
            self._stack.addWidget(page)

    # ------------------------------------------------------------------ pages

    @staticmethod
    def _card() -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setProperty("card", True)
        card.setMinimumWidth(360)
        card.setMaximumWidth(560)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)
        return card, layout

    @staticmethod
    def _label(
        text: str = "", *, heading: bool = False, muted: bool = False, error: bool = False
    ) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setProperty("heading", heading)
        label.setProperty("muted", muted)
        label.setProperty("error", error)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _page_empty(self) -> QFrame:
        card, layout = self._card()
        layout.addWidget(self._label(tr("No connection selected"), heading=True))
        layout.addWidget(
            self._label(
                tr("Pick a database in the list on the right, or create a connection."), muted=True
            )
        )
        button = QPushButton(tr("Connections…"))
        button.setProperty("primary", True)
        button.clicked.connect(self.manageRequested)
        row = QHBoxLayout()
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _page_connecting(self) -> QFrame:
        card, layout = self._card()
        self._connecting_title = self._label(heading=True)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(lambda: self._emit_id(self.cancelRequested))
        layout.addWidget(self._connecting_title)
        layout.addWidget(bar)
        row = QHBoxLayout()
        row.addWidget(cancel)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _page_error(self) -> QFrame:
        card, layout = self._card()
        self._error_title = self._label(heading=True)
        self._error_message = self._label(error=True)
        self._error_hint = self._label(muted=True)
        retry = QPushButton(tr("Retry"))
        retry.setProperty("primary", True)
        retry.clicked.connect(lambda: self._emit_id(self.retryRequested))
        edit = QPushButton(tr("Edit connection…"))
        edit.clicked.connect(lambda: self._emit_id(self.editRequested))
        layout.addWidget(self._error_title)
        layout.addWidget(self._error_message)
        layout.addWidget(self._error_hint)
        row = QHBoxLayout()
        row.addWidget(retry)
        row.addWidget(edit)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _page_ready(self) -> QFrame:
        card, layout = self._card()
        self._ready_title = self._label(heading=True)
        self._ready_badge = self._label(error=True)
        layout.addWidget(self._ready_title)
        layout.addWidget(self._ready_badge)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self._ready_values: dict[str, QLabel] = {}
        for row, (key, caption) in enumerate(
            [("system", tr("Database system")), ("address", tr("Address")), ("mode", tr("Mode"))]
        ):
            grid.addWidget(self._label(caption, muted=True), row, 0, Qt.AlignmentFlag.AlignTop)
            value = self._label()
            self._ready_values[key] = value
            grid.addWidget(value, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        disconnect = QPushButton(tr("Disconnect"))
        disconnect.clicked.connect(lambda: self._emit_id(self.disconnectRequested))
        manage = QPushButton(tr("Connections…"))
        manage.clicked.connect(self.manageRequested)
        row_layout = QHBoxLayout()
        row_layout.addWidget(disconnect)
        row_layout.addWidget(manage)
        row_layout.addStretch(1)
        layout.addLayout(row_layout)
        return card

    def _emit_id(self, signal: SignalInstance) -> None:
        if self._connection_id is not None:
            signal.emit(self._connection_id)

    # ------------------------------------------------------------------ state

    def show_session(self, session: Session | None) -> None:
        """Show the page matching the session's state (``None`` = nothing selected)."""
        if session is None:
            self._connection_id = None
            self._stack.setCurrentWidget(self._empty)
            return
        config = session.config
        self._connection_id = config.id
        if session.state is SessionState.CONNECTING:
            self._connecting_title.setText(tr("Connecting to {name}…", name=config.name))
            self._stack.setCurrentWidget(self._connecting)
        elif session.state is SessionState.ERROR:
            self._error_title.setText(tr("Could not connect to {name}", name=config.name))
            self._error_message.setText(str(session.error))
            hint = hint_for(session.error)
            self._error_hint.setText(hint)
            self._error_hint.setVisible(bool(hint))
            self._stack.setCurrentWidget(self._error)
        elif session.state is SessionState.READY:
            self._fill_ready(session)
            self._stack.setCurrentWidget(self._ready)
        else:
            self._connecting_title.setText(tr("{name} is not connected", name=config.name))
            self._error_title.setText(tr("{name} is not connected", name=config.name))
            self._error_message.setText("")
            self._error_hint.setText("")
            self._stack.setCurrentWidget(self._error)

    def _fill_ready(self, session: Session) -> None:
        config = session.config
        info = session.server_info
        self._ready_title.setText(config.name)
        self._ready_badge.setText(tr("PRODUCTION") if config.production else "")
        self._ready_badge.setVisible(config.production)
        if info is not None:
            version = ".".join(str(part) for part in info.version)
            system = f"{_FLAVORS.get(info.flavor, info.flavor)} {version}".strip()
        else:
            system = ""
        self._ready_values["system"].setText(system)
        self._ready_values["address"].setText(config.subtitle)
        self._ready_values["mode"].setText(
            tr("Read-only") if config.read_only else tr("Read and write")
        )

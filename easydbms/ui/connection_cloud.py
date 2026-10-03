"""The "Cloud" tab of the connection form: pick a hosted service and fill in its settings."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from ..core.connections import ProviderConfig, ProviderKind
from ..core.connections.providers import Provider, ProviderField, get_provider, providers_for
from ..core.dialects import DialectId
from .i18n import tr


def provider_title(kind: ProviderKind) -> str:
    return {
        ProviderKind.AWS_RDS: tr("AWS RDS / Aurora (IAM)"),
        ProviderKind.GCP_CLOUDSQL: tr("Google Cloud SQL (IAM)"),
        ProviderKind.AZURE: tr("Azure Database (Microsoft Entra ID)"),
        ProviderKind.SUPABASE: tr("Supabase"),
        ProviderKind.NEON: tr("Neon"),
        ProviderKind.PLANETSCALE: tr("PlanetScale"),
        ProviderKind.COCKROACH: tr("CockroachDB Cloud"),
    }[kind]


def provider_note(kind: ProviderKind) -> str:
    return {
        ProviderKind.AWS_RDS: tr(
            "No password: a 15-minute token is created from your AWS credentials every time the "
            "connection opens. The database user must be enabled for IAM authentication."
        ),
        ProviderKind.GCP_CLOUDSQL: tr(
            "No password: your Google account's access token is the password. The user is the "
            "IAM principal (a PostgreSQL service account without .gserviceaccount.com, a MySQL "
            "user without the domain). Use the instance IP or a running Cloud SQL Auth Proxy."
        ),
        ProviderKind.AZURE: tr(
            "No password: an Entra access token is requested every time the connection opens. "
            "The user is the Entra principal (for PostgreSQL usually name@tenant.onmicrosoft.com)."
        ),
        ProviderKind.SUPABASE: tr(
            "PostgreSQL. Direct connection to db.<project-ref>.supabase.co with the password set "
            "in Project Settings → Database. TLS is required."
        ),
        ProviderKind.NEON: tr(
            "PostgreSQL. Paste the host from the Neon console's connection details "
            "(ep-….neon.tech, or the -pooler one). TLS is required."
        ),
        ProviderKind.PLANETSCALE: tr(
            "MySQL. The host, user and password come from the branch's Connect dialog. TLS with a "
            "verified certificate is required."
        ),
        ProviderKind.COCKROACH: tr(
            "PostgreSQL protocol, port 26257. For a Serverless cluster the cluster name goes in "
            "front of the database name. Download the cluster's CA certificate and choose it in "
            "the SSL / TLS tab for full verification."
        ),
    }[kind]


def field_label(name: str) -> str:
    return {
        "region": tr("AWS region"),
        "profile": tr("AWS profile"),
        "client_id": tr("Managed identity client ID"),
        "service_account_file": tr("Service account key file"),
        "project_ref": tr("Project reference"),
        "endpoint": tr("Endpoint host"),
        "host": tr("Host"),
        "cluster": tr("Cluster name (Serverless)"),
    }.get(name, name)


def field_placeholder(name: str) -> str:
    return {
        "region": tr("eu-west-1 (read from the host name if empty)"),
        "profile": tr("default"),
        "client_id": tr("only for a user-assigned identity"),
        "service_account_file": tr("optional (JSON)"),
    }.get(name, "")


class CloudEditor(QWidget):
    edited = Signal()
    #: The person picked another service: its kind value, or ``""`` for none.
    chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dialect = DialectId.POSTGRESQL
        self._loading = False
        self._inputs: dict[str, QLineEdit] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel(tr("Hosted service"))
        label.setProperty("muted", True)
        self.combo = QComboBox()
        self.note = QLabel()
        self.note.setProperty("muted", True)
        self.note.setWordWrap(True)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)
        layout.addWidget(self.combo)
        layout.addWidget(self.note)
        layout.addWidget(self.form_host)
        layout.addStretch(1)
        self._fill_combo()
        self.combo.activated.connect(self._on_activated)
        self._show_provider(None)

    # ------------------------------------------------------------------ state

    @property
    def kind(self) -> ProviderKind | None:
        data = self.combo.currentData()
        return ProviderKind(data) if data else None

    @property
    def provider(self) -> Provider | None:
        kind = self.kind
        return get_provider(kind) if kind else None

    @property
    def uses_token(self) -> bool:
        provider = self.provider
        return provider is not None and provider.uses_token

    @property
    def params(self) -> dict[str, str]:
        return {
            name: edit.text().strip() for name, edit in self._inputs.items() if edit.text().strip()
        }

    def set_dialect(self, dialect_id: DialectId) -> None:
        """Offer only the services of this database system; drop a choice that no longer fits."""
        self._dialect = dialect_id
        current = self.kind
        self._fill_combo()
        if current is not None and dialect_id in get_provider(current).dialects:
            self.combo.setCurrentIndex(max(0, self.combo.findData(current.value)))
        else:
            self.combo.setCurrentIndex(0)
            self._show_provider(None)

    def load(self, provider: ProviderConfig | None) -> None:
        self._loading = True
        try:
            self._fill_combo()
            kind = provider.kind if provider else None
            if kind is not None and self.combo.findData(kind.value) < 0:
                self.combo.addItem(provider_title(kind), kind.value)  # a service of another dialect
            self.combo.setCurrentIndex(max(0, self.combo.findData(kind.value if kind else "")))
            self._show_provider(kind)
            for name, value in (provider.params if provider else {}).items():
                if name in self._inputs:
                    self._inputs[name].setText(value)
        finally:
            self._loading = False

    def read(self) -> dict[str, Any] | None:
        kind = self.kind
        if kind is None:
            return None
        return {"kind": kind.value, "params": self.params}

    # ------------------------------------------------------------------ internals

    def _fill_combo(self) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(tr("None — a regular server"), "")
        for provider in providers_for(self._dialect):
            self.combo.addItem(provider_title(provider.kind), provider.kind.value)
        self.combo.blockSignals(False)

    def _on_activated(self, _index: int) -> None:
        self._show_provider(self.kind)
        if not self._loading:
            self.chosen.emit(self.kind.value if self.kind else "")
            self.edited.emit()

    def _show_provider(self, kind: ProviderKind | None) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._inputs.clear()
        if kind is None:
            self.note.setText(
                tr(
                    "Pick AWS, Google Cloud, Azure, Supabase, Neon, PlanetScale or CockroachDB "
                    "to get its settings filled in."
                )
            )
            return
        provider = get_provider(kind)
        self.note.setText(provider_note(kind))
        for spec in provider.fields:
            self._add_field(spec)

    def _add_field(self, spec: ProviderField) -> None:
        edit = QLineEdit()
        edit.setPlaceholderText(field_placeholder(spec.name) or spec.placeholder)
        edit.textEdited.connect(lambda _text: self.edited.emit())
        self._inputs[spec.name] = edit
        label = QLabel(field_label(spec.name))
        label.setProperty("muted", True)
        self.form.addRow(label, edit)

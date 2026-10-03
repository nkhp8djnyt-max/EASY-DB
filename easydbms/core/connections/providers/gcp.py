"""Google Cloud SQL with IAM database authentication.

The OAuth2 access token of the account (``sqlservice.admin`` scope) is the password; it comes from
Application Default Credentials (``gcloud auth application-default login``, a service account
file, the metadata server). Connect to the instance's IP (TLS is required), or to a running
Cloud SQL Auth Proxy on 127.0.0.1.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..models import ProviderKind, ServerConnection
from .base import (
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderField,
    ProviderUnavailable,
    Token,
)

SCOPES = ["https://www.googleapis.com/auth/sqlservice.admin"]


class GoogleCloudSql(Provider):
    kind = ProviderKind.GCP_CLOUDSQL
    title = "Google Cloud SQL (IAM)"
    note = (
        "No password: your Google account's access token is the password. The user is the IAM "
        "principal (a PostgreSQL service account without .gserviceaccount.com, a MySQL user "
        "without the domain). Use the instance IP or a running Cloud SQL Auth Proxy."
    )
    fields = (ProviderField("service_account_file", "Service account key file", "optional (JSON)"),)
    uses_token = True

    def token(
        self, config: ServerConnection, params: Mapping[str, str], environ: Mapping[str, str]
    ) -> Token:
        if not config.user:
            raise ProviderError("enter the IAM user the database knows")
        google_auth, transport, exceptions, service_account = _sdk()
        key_file = params.get("service_account_file", "").strip()
        try:
            if key_file:
                credentials = service_account.Credentials.from_service_account_file(
                    key_file, scopes=SCOPES
                )
            else:
                credentials, _project = google_auth.default(scopes=SCOPES)
            credentials.refresh(transport.Request())
        except FileNotFoundError:
            raise ProviderAuthError(f"the key file '{key_file}' does not exist") from None
        except exceptions.DefaultCredentialsError:
            raise ProviderAuthError(
                "no Google credentials found: run `gcloud auth application-default login` or "
                "choose a service account key file"
            ) from None
        except (exceptions.GoogleAuthError, ValueError) as error:
            raise ProviderAuthError(f"Google did not issue a token: {error}") from None
        expiry = credentials.expiry
        expires_at = (
            expiry.replace(tzinfo=UTC).timestamp()
            if isinstance(expiry, datetime)
            else time.time() + 55 * 60
        )
        return Token(str(credentials.token), expires_at)

    def describe(self, token: Token | None) -> str:
        return "Google access token obtained"


def _sdk() -> tuple[Any, Any, Any, Any]:
    try:
        import google.auth
        import google.auth.exceptions
        import google.auth.transport.requests
        import google.oauth2.service_account
    except ImportError:
        raise ProviderUnavailable("google-auth", "gcp") from None
    return (
        google.auth,
        google.auth.transport.requests,
        google.auth.exceptions,
        google.oauth2.service_account,
    )

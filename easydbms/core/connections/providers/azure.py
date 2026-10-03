"""Azure Database for PostgreSQL / MySQL with Microsoft Entra ID.

The Entra access token for the ``ossrdbms-aad`` resource is the password. Credentials are found
the way ``azure-identity`` does it: environment, managed identity, Azure CLI, VS Code ...
"""

from __future__ import annotations

from collections.abc import Mapping
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

SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


class AzureEntra(Provider):
    kind = ProviderKind.AZURE
    title = "Azure Database (Microsoft Entra ID)"
    note = (
        "No password: an Entra access token is requested every time the connection opens. "
        "The user is the Entra principal (for PostgreSQL usually name@tenant.onmicrosoft.com)."
    )
    fields = (
        ProviderField(
            "client_id", "Managed identity client ID", "only for a user-assigned identity"
        ),
    )
    uses_token = True

    def token(
        self, config: ServerConnection, params: Mapping[str, str], environ: Mapping[str, str]
    ) -> Token:
        if not config.user:
            raise ProviderError("enter the Entra user the database knows (the principal name)")
        identity, core = _sdk()
        client_id = params.get("client_id", "").strip()
        try:
            credential = identity.DefaultAzureCredential(
                managed_identity_client_id=client_id or None
            )
            access = credential.get_token(SCOPE)
        except core.exceptions.ClientAuthenticationError as error:
            raise ProviderAuthError(
                f"Azure did not issue a token ({error}); sign in with `az login` or set the "
                "AZURE_* environment variables"
            ) from None
        return Token(str(access.token), float(access.expires_on))

    def describe(self, token: Token | None) -> str:
        return "Entra ID token obtained"


def _sdk() -> tuple[Any, Any]:
    try:
        import azure.core
        import azure.core.exceptions
        import azure.identity
    except ImportError:
        raise ProviderUnavailable("azure-identity", "azure") from None
    return azure.identity, azure.core

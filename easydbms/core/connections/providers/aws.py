"""Amazon RDS / Aurora with IAM database authentication.

The token is a presigned request that RDS accepts as the password for 15 minutes; creating it is a
local computation (no network call) from the AWS credentials found the usual way: environment,
``~/.aws``, SSO, an instance role.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from ...dialects import DialectId
from ..models import ProviderKind, ServerConnection, SslMode
from .base import (
    Defaults,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderField,
    ProviderUnavailable,
    Token,
)

_REGION_IN_HOST = re.compile(r"\.([a-z]{2}(?:-[a-z]+)+-\d)\.rds\.amazonaws\.com(?:\.cn)?$")
#: RDS accepts a token for 15 minutes; renew a little before.
_LIFETIME = 14 * 60


class AwsRds(Provider):
    kind = ProviderKind.AWS_RDS
    title = "AWS RDS / Aurora (IAM)"
    note = (
        "No password: a 15-minute token is created from your AWS credentials every time the "
        "connection opens. The database user must be enabled for IAM authentication."
    )
    fields = (
        ProviderField("region", "AWS region", "eu-west-1 (read from the host name if empty)"),
        ProviderField("profile", "AWS profile", "default"),
    )
    uses_token = True

    def defaults(self, params: Mapping[str, str], dialect: DialectId) -> Defaults:
        return Defaults(ssl_mode=SslMode.REQUIRE)

    def token(
        self, config: ServerConnection, params: Mapping[str, str], environ: Mapping[str, str]
    ) -> Token:
        if not config.user:
            raise ProviderError("enter the database user: an IAM token is issued for one user")
        port = config.effective_port
        assert port is not None
        host = config.host
        region = (
            params.get("region", "").strip()
            or _region_of(host)
            or environ.get("AWS_REGION", "")
            or environ.get("AWS_DEFAULT_REGION", "")
        )
        profile = params.get("profile", "").strip() or environ.get("AWS_PROFILE", "")
        boto3, botocore = _sdk()
        try:
            session = boto3.Session(profile_name=profile or None, region_name=region or None)
            if session.get_credentials() is None:
                raise ProviderAuthError(
                    "no AWS credentials found (environment variables, ~/.aws, SSO or a role)"
                )
            if not session.region_name:
                raise ProviderError("the AWS region is unknown: enter it in the Cloud tab")
            token = session.client("rds").generate_db_auth_token(
                DBHostname=host, Port=port, DBUsername=config.user, Region=session.region_name
            )
        except botocore.exceptions.ProfileNotFound:
            raise ProviderAuthError(f"the AWS profile '{profile}' does not exist") from None
        except botocore.exceptions.BotoCoreError as error:
            raise ProviderAuthError(f"AWS did not provide credentials: {error}") from None
        return Token(str(token), time.time() + _LIFETIME)

    def describe(self, token: Token | None) -> str:
        return "IAM token created (valid for 15 minutes)"


def _region_of(host: str) -> str:
    match = _REGION_IN_HOST.search(host)
    return match.group(1) if match else ""


def _sdk() -> tuple[Any, Any]:
    try:
        import boto3
        import botocore.exceptions
    except ImportError:
        raise ProviderUnavailable("boto3", "aws") from None
    return boto3, botocore

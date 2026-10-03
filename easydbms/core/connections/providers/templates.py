"""Hosted databases that only need the right host, port and TLS settings.

Supabase, Neon, PlanetScale and CockroachDB Cloud connect with an ordinary user and password; the
plugin fills in what their documentation prescribes and adds a short hint.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...dialects import DialectId
from ..models import ProviderKind, SslMode
from .base import Defaults, Provider, ProviderField, Token


class Supabase(Provider):
    kind = ProviderKind.SUPABASE
    title = "Supabase"
    note = (
        "PostgreSQL. Direct connection to db.<project-ref>.supabase.co with the password set in "
        "Project Settings → Database. TLS is required."
    )
    fields = (ProviderField("project_ref", "Project reference", "abcdefghijklmnop", required=True),)
    dialects = (DialectId.POSTGRESQL,)

    def defaults(self, params: Mapping[str, str], dialect: DialectId) -> Defaults:
        ref = params.get("project_ref", "").strip()
        return Defaults(
            dialect=DialectId.POSTGRESQL,
            host=f"db.{ref}.supabase.co" if ref else None,
            port=5432,
            user="postgres",
            database="postgres",
            ssl_mode=SslMode.REQUIRE,
        )

    def describe(self, token: Token | None) -> str:
        return "Supabase settings applied"


class Neon(Provider):
    kind = ProviderKind.NEON
    title = "Neon"
    note = (
        "PostgreSQL. Paste the host from the Neon console's connection details "
        "(ep-….neon.tech, or the -pooler one). TLS is required."
    )
    fields = (ProviderField("endpoint", "Endpoint host", "ep-cool-darkness-123456.neon.tech"),)
    dialects = (DialectId.POSTGRESQL,)

    def defaults(self, params: Mapping[str, str], dialect: DialectId) -> Defaults:
        host = params.get("endpoint", "").strip()
        return Defaults(
            dialect=DialectId.POSTGRESQL,
            host=host or None,
            port=5432,
            database="neondb",
            ssl_mode=SslMode.REQUIRE,
        )

    def describe(self, token: Token | None) -> str:
        return "Neon settings applied"


class PlanetScale(Provider):
    kind = ProviderKind.PLANETSCALE
    title = "PlanetScale"
    note = (
        "MySQL. The host, user and password come from the branch's Connect dialog. TLS with a "
        "verified certificate is required."
    )
    fields = (ProviderField("host", "Host", "aws.connect.psdb.cloud"),)
    dialects = (DialectId.MYSQL,)

    def defaults(self, params: Mapping[str, str], dialect: DialectId) -> Defaults:
        return Defaults(
            dialect=DialectId.MYSQL,
            host=params.get("host", "").strip() or None,
            port=3306,
            ssl_mode=SslMode.VERIFY_FULL,
        )

    def describe(self, token: Token | None) -> str:
        return "PlanetScale settings applied"


class CockroachCloud(Provider):
    kind = ProviderKind.COCKROACH
    title = "CockroachDB Cloud"
    note = (
        "PostgreSQL protocol, port 26257. For a Serverless cluster the cluster name goes in "
        "front of the database name. Download the cluster's CA certificate and choose it in the "
        "SSL / TLS tab for full verification."
    )
    fields = (
        ProviderField("cluster", "Cluster name (Serverless)", "my-cluster-1234"),
        ProviderField("host", "Host", "my-cluster-1234.abc.cockroachlabs.cloud"),
    )
    dialects = (DialectId.POSTGRESQL,)

    def defaults(self, params: Mapping[str, str], dialect: DialectId) -> Defaults:
        cluster = params.get("cluster", "").strip()
        return Defaults(
            dialect=DialectId.POSTGRESQL,
            host=params.get("host", "").strip() or None,
            port=26257,
            database=f"{cluster}.defaultdb" if cluster else "defaultdb",
            ssl_mode=SslMode.VERIFY_FULL,
        )

    def describe(self, token: Token | None) -> str:
        return "CockroachDB Cloud settings applied"

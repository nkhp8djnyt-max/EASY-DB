"""Cloud provider plugins: defaults, tokens (AWS for real, Azure / Google through fake SDKs)."""

from __future__ import annotations

import sys
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from easydbms.core.connections import (
    ConnectionStore,
    ProviderConfig,
    ProviderKind,
    ServerConnection,
    SslMode,
    resolve_config,
)
from easydbms.core.connections.providers import (
    AwsRds,
    AzureEntra,
    GoogleCloudSql,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    Token,
    all_providers,
    get_provider,
    providers_for,
)
from easydbms.core.dialects import DialectId


def server(kind: ProviderKind, host: str = "db.example.com", **extra: Any) -> ServerConnection:
    data: dict[str, Any] = {
        "name": "cloud",
        "dialect": "postgresql",
        "host": host,
        "user": "app",
        "provider": {"kind": kind, "params": extra.pop("params", {})},
    }
    return ServerConnection.model_validate(data | extra)


# ---------------------------------------------------------------------------- registry


def test_every_kind_has_a_plugin_and_the_dialects_it_supports() -> None:
    assert {p.kind for p in all_providers()} == set(ProviderKind)
    assert get_provider("aws-rds").title.startswith("AWS RDS")
    postgres = {p.kind for p in providers_for(DialectId.POSTGRESQL)}
    mysql = {p.kind for p in providers_for(DialectId.MYSQL)}
    assert ProviderKind.SUPABASE in postgres
    assert ProviderKind.SUPABASE not in mysql
    assert ProviderKind.PLANETSCALE in mysql
    assert ProviderKind.PLANETSCALE not in postgres
    assert {ProviderKind.AWS_RDS, ProviderKind.AZURE, ProviderKind.GCP_CLOUDSQL} <= postgres & mysql
    assert {p.kind for p in all_providers() if p.uses_token} == {
        ProviderKind.AWS_RDS,
        ProviderKind.AZURE,
        ProviderKind.GCP_CLOUDSQL,
    }


def test_required_settings_are_reported() -> None:
    supabase = get_provider("supabase")
    assert [f.name for f in supabase.missing({})] == ["project_ref"]
    assert supabase.missing({"project_ref": " abc "}) == []


def test_a_token_knows_when_it_is_about_to_expire() -> None:
    assert Token("x").valid()
    assert Token("x", time.time() + 3600).valid()
    assert not Token("x", time.time() + 30).valid()  # inside the one-minute margin
    assert not Token("x", time.time() - 1).valid()
    assert Token("x", 100.0).valid(margin=0, now=50.0)
    assert "secret" not in repr(Token("secret"))


# ---------------------------------------------------------------------------- templates


def test_supabase_fills_the_host_from_the_project_reference() -> None:
    defaults = get_provider("supabase").defaults({"project_ref": "abcd1234"}, DialectId.POSTGRESQL)
    assert (defaults.dialect, defaults.host, defaults.port) == (
        DialectId.POSTGRESQL,
        "db.abcd1234.supabase.co",
        5432,
    )
    assert (defaults.user, defaults.database, defaults.ssl_mode) == (
        "postgres",
        "postgres",
        SslMode.REQUIRE,
    )
    assert get_provider("supabase").defaults({}, DialectId.POSTGRESQL).host is None


def test_neon_planetscale_and_cockroach_defaults() -> None:
    neon = get_provider("neon").defaults({"endpoint": "ep-x.neon.tech"}, DialectId.POSTGRESQL)
    assert (neon.host, neon.database, neon.ssl_mode) == (
        "ep-x.neon.tech",
        "neondb",
        SslMode.REQUIRE,
    )
    planet = get_provider("planetscale").defaults({}, DialectId.MYSQL)
    assert (planet.dialect, planet.port, planet.ssl_mode) == (
        DialectId.MYSQL,
        3306,
        SslMode.VERIFY_FULL,
    )
    cockroach = get_provider("cockroachdb").defaults(
        {"cluster": "my-cluster-12", "host": "h.cockroachlabs.cloud"}, DialectId.POSTGRESQL
    )
    assert (cockroach.port, cockroach.database, cockroach.ssl_mode) == (
        26257,
        "my-cluster-12.defaultdb",
        SslMode.VERIFY_FULL,
    )
    assert get_provider("cockroachdb").defaults({}, DialectId.POSTGRESQL).database == "defaultdb"


def test_templates_do_not_create_tokens() -> None:
    with pytest.raises(NotImplementedError):
        get_provider("neon").token(server(ProviderKind.NEON), {}, {})


# ---------------------------------------------------------------------------- AWS (real boto3)


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def test_an_rds_token_is_a_presigned_connect_request(aws_credentials: None) -> None:
    host = "db.abc.eu-west-1.rds.amazonaws.com"
    config = server(ProviderKind.AWS_RDS, host)
    token = AwsRds().token(config, {}, {})  # the region is read from the host name
    assert token.password.startswith(f"{host}:5432/?Action=connect&DBUser=app&")
    assert "X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F" in token.password
    assert "%2Feu-west-1%2Frds-db%2Faws4_request" in token.password
    assert token.expires_at is not None
    assert 13 * 60 < token.expires_at - time.time() <= 14 * 60
    assert token.valid()


def test_the_port_follows_the_dialect_and_an_explicit_region_wins(aws_credentials: None) -> None:
    config = server(
        ProviderKind.AWS_RDS,
        "mydb.example.internal",
        dialect="mysql",
        params={"region": "us-east-2"},
    )
    token = AwsRds().token(config, {"region": "us-east-2"}, {})
    assert token.password.startswith("mydb.example.internal:3306/?")
    assert "%2Fus-east-2%2Frds-db" in token.password


def test_the_region_may_come_from_the_environment(aws_credentials: None) -> None:
    config = server(ProviderKind.AWS_RDS, "mydb.example.internal")
    token = AwsRds().token(config, {}, {"AWS_REGION": "ap-south-1"})
    assert "%2Fap-south-1%2Frds-db" in token.password


def test_an_unknown_region_credentials_user_and_profile_are_explained(
    aws_credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = server(ProviderKind.AWS_RDS, "mydb.example.internal")
    with pytest.raises(ProviderError, match="region is unknown"):
        AwsRds().token(plain, {}, {})
    with pytest.raises(ProviderAuthError, match="profile 'nope' does not exist"):
        AwsRds().token(plain, {"profile": "nope", "region": "eu-west-1"}, {})
    with pytest.raises(ProviderError, match="enter the database user"):
        AwsRds().token(server(ProviderKind.AWS_RDS, user=""), {"region": "eu-west-1"}, {})
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    with pytest.raises(ProviderAuthError, match="no AWS credentials"):
        AwsRds().token(plain, {"region": "eu-west-1"}, {})


def test_a_missing_sdk_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)  # makes ``import boto3`` fail
    with pytest.raises(ProviderUnavailable, match=r"easydbms\[aws\]") as raised:
        AwsRds().token(server(ProviderKind.AWS_RDS), {}, {})
    assert (raised.value.package, raised.value.extra) == ("boto3", "aws")
    assert isinstance(raised.value, ProviderError)


# ---------------------------------------------------------------------------- Azure (fake SDK)


class _Rejected(Exception):
    pass


def install_azure(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    azure = types.ModuleType("azure")
    core = types.ModuleType("azure.core")
    exceptions = types.ModuleType("azure.core.exceptions")
    exceptions.ClientAuthenticationError = _Rejected  # type: ignore[attr-defined]
    identity = types.ModuleType("azure.identity")

    class DefaultAzureCredential:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

        def get_token(self, scope: str) -> Any:
            calls.append({"scope": scope})
            if fail:
                raise _Rejected("no credential in the chain worked")
            return types.SimpleNamespace(token="entra-token", expires_on=time.time() + 3000)

    identity.DefaultAzureCredential = DefaultAzureCredential  # type: ignore[attr-defined]
    azure.core = core  # type: ignore[attr-defined]
    azure.identity = identity  # type: ignore[attr-defined]
    core.exceptions = exceptions  # type: ignore[attr-defined]
    for name, module in (
        ("azure", azure),
        ("azure.core", core),
        ("azure.core.exceptions", exceptions),
        ("azure.identity", identity),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_an_entra_token_is_requested_for_the_database_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_azure(monkeypatch)
    token = AzureEntra().token(
        server(ProviderKind.AZURE, user="me@contoso.onmicrosoft.com"), {"client_id": "abc"}, {}
    )
    assert token.password == "entra-token"
    assert token.valid()
    assert calls[0] == {"managed_identity_client_id": "abc"}
    assert calls[1] == {"scope": "https://ossrdbms-aad.database.windows.net/.default"}


def test_azure_failures_are_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    install_azure(monkeypatch, fail=True)
    with pytest.raises(ProviderAuthError, match="az login"):
        AzureEntra().token(server(ProviderKind.AZURE), {}, {})
    with pytest.raises(ProviderError, match="Entra user"):
        AzureEntra().token(server(ProviderKind.AZURE, user=""), {}, {})
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    with pytest.raises(ProviderUnavailable, match=r"easydbms\[azure\]"):
        AzureEntra().token(server(ProviderKind.AZURE), {}, {})


# ---------------------------------------------------------------------------- Google (fake SDK)


class _NoCredentials(Exception):
    pass


class _GoogleError(Exception):
    pass


def install_google(
    monkeypatch: pytest.MonkeyPatch, *, found: bool = True, expiry: datetime | None = None
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class Credentials:
        token = "google-token"

        def __init__(self) -> None:
            self.expiry = expiry

        def refresh(self, request: object) -> None:
            seen["refreshed"] = request

    def default(scopes: list[str]) -> tuple[Credentials, str]:
        seen["scopes"] = scopes
        if not found:
            raise _NoCredentials("none")
        return Credentials(), "project"

    class ServiceCredentials:
        @staticmethod
        def from_service_account_file(path: str, scopes: list[str]) -> Credentials:
            seen["file"] = path
            if path.endswith("missing.json"):
                raise FileNotFoundError(path)
            return Credentials()

    modules: dict[str, types.ModuleType] = {}
    for name in (
        "google",
        "google.auth",
        "google.auth.exceptions",
        "google.auth.transport",
        "google.auth.transport.requests",
        "google.oauth2",
        "google.oauth2.service_account",
    ):
        modules[name] = types.ModuleType(name)
    modules["google.auth"].default = default  # type: ignore[attr-defined]
    modules["google.auth.exceptions"].DefaultCredentialsError = _NoCredentials  # type: ignore[attr-defined]
    modules["google.auth.exceptions"].GoogleAuthError = _GoogleError  # type: ignore[attr-defined]
    modules["google.auth.transport.requests"].Request = lambda: "request"  # type: ignore[attr-defined]
    modules["google.oauth2.service_account"].Credentials = ServiceCredentials  # type: ignore[attr-defined]
    modules["google"].auth = modules["google.auth"]  # type: ignore[attr-defined]
    modules["google.auth"].exceptions = modules["google.auth.exceptions"]  # type: ignore[attr-defined]
    modules["google.auth"].transport = modules["google.auth.transport"]  # type: ignore[attr-defined]
    modules["google.auth.transport"].requests = modules["google.auth.transport.requests"]  # type: ignore[attr-defined]
    modules["google"].oauth2 = modules["google.oauth2"]  # type: ignore[attr-defined]
    modules["google.oauth2"].service_account = modules["google.oauth2.service_account"]  # type: ignore[attr-defined]
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return seen


def test_a_google_token_comes_from_application_default_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(
        minutes=50
    )  # google-auth: naive UTC
    seen = install_google(monkeypatch, expiry=expiry)
    token = GoogleCloudSql().token(server(ProviderKind.GCP_CLOUDSQL), {}, {})
    assert token.password == "google-token"
    assert seen["scopes"] == ["https://www.googleapis.com/auth/sqlservice.admin"]
    assert seen["refreshed"] == "request"
    assert token.expires_at is not None
    assert 45 * 60 < token.expires_at - time.time() < 51 * 60


def test_a_service_account_key_file_is_used_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_google(monkeypatch)
    token = GoogleCloudSql().token(
        server(ProviderKind.GCP_CLOUDSQL), {"service_account_file": "/keys/sa.json"}, {}
    )
    assert seen["file"] == "/keys/sa.json"
    assert token.expires_at is not None  # no expiry reported: a conservative default


def test_google_failures_are_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    install_google(monkeypatch, found=False)
    with pytest.raises(ProviderAuthError, match="gcloud auth application-default login"):
        GoogleCloudSql().token(server(ProviderKind.GCP_CLOUDSQL), {}, {})
    with pytest.raises(ProviderAuthError, match=r"missing\.json"):
        GoogleCloudSql().token(
            server(ProviderKind.GCP_CLOUDSQL), {"service_account_file": "/x/missing.json"}, {}
        )
    with pytest.raises(ProviderError, match="IAM user"):
        GoogleCloudSql().token(server(ProviderKind.GCP_CLOUDSQL, user=""), {}, {})
    monkeypatch.setitem(sys.modules, "google.auth", None)
    with pytest.raises(ProviderUnavailable, match=r"easydbms\[gcp\]"):
        GoogleCloudSql().token(server(ProviderKind.GCP_CLOUDSQL), {}, {})


# ---------------------------------------------------------------------------- the model


def test_provider_settings_survive_the_json_file_and_hold_no_secret(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "c.json")
    config = server(ProviderKind.AWS_RDS, params={"region": "eu-west-1", "profile": "prod"})
    store.save(config)
    text = (tmp_path / "c.json").read_text()
    assert '"aws-rds"' in text
    assert "eu-west-1" in text
    assert "token" not in text.lower()
    assert text.lower().count("password") == 1  # only the "save_password" flag
    assert ConnectionStore(tmp_path / "c.json").get(config.id) == config


def test_unknown_provider_kinds_and_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="aws-rds"):
        ProviderConfig.model_validate({"kind": "heroku"})
    with pytest.raises(ValueError, match="password"):
        ProviderConfig.model_validate({"kind": "neon", "password": "x"})
    assert ProviderConfig(kind=ProviderKind.NEON, params={" a ": " b ", " ": "x"}).params == {
        "a": "b"
    }


def test_provider_settings_expand_environment_variables() -> None:
    config = server(ProviderKind.AWS_RDS, params={"region": "${REGION}", "profile": "${P:-dev}"})
    resolved, _ = resolve_config(config, None, {"REGION": "eu-north-1"})
    assert isinstance(resolved, ServerConnection)
    assert resolved.provider is not None
    assert resolved.provider.params == {"region": "eu-north-1", "profile": "dev"}
    with pytest.raises(ValueError, match="REGION"):
        resolve_config(config, None, {})

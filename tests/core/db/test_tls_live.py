"""TLS against real servers. Needs a PostgreSQL / MariaDB set up with the test PKI.

Set ``EASYDBMS_TEST_TLS_DIR`` to the directory written by ``python -m tests.support.certs`` and
run the servers with its ``server.pem`` / ``server.key`` (see "Testing TLS" in the README); the
users ``erd_cert`` (client certificate with CN ``erd_cert`` required, no password) must exist.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from easydbms.core.connections import ServerConnection, SslConfig, SslMode, replace
from easydbms.core.db import (
    ConnectionFailed,
    ConnectRuntime,
    Route,
    SslError,
    SslFileError,
    create_client,
)
from tests.core.conftest import Target
from tests.support.certs import PASSPHRASE


@pytest.fixture
def pki() -> dict[str, str]:
    directory = os.environ.get("EASYDBMS_TEST_TLS_DIR")
    if not directory:
        pytest.skip("set EASYDBMS_TEST_TLS_DIR to run the TLS tests against real servers")
    return {
        name: str(Path(directory) / name)
        for name in (
            "ca.pem",
            "other-ca.pem",
            "client.pem",
            "client.key",
            "client-encrypted.key",
        )
    }


@pytest.fixture
def server(target: Target) -> ServerConnection:
    if not isinstance(target.config, ServerConnection):
        pytest.skip("server connections only")
    return target.config


def with_ssl(server: ServerConnection, mode: SslMode | None, **files: str) -> ServerConnection:
    changed = replace(server, ssl=SslConfig(mode=mode, **files))
    assert isinstance(changed, ServerConnection)
    return changed


def as_cert_user(server: ServerConnection, ssl: SslConfig) -> ServerConnection:
    changed = replace(server, user="erd_cert", ssl=ssl)
    assert isinstance(changed, ServerConnection)
    return changed


def test_require_encrypts_the_connection(
    target: Target, server: ServerConnection, pki: dict[str, str]
) -> None:
    check = create_client(with_ssl(server, SslMode.REQUIRE), target.password).test()
    assert check.ok, check.failure
    tls = next(step for step in check.steps if step.name == "TLS")
    assert tls.ok
    assert "TLSv1" in tls.detail


def test_disable_leaves_the_connection_plain(
    target: Target, server: ServerConnection, pki: dict[str, str]
) -> None:
    check = create_client(with_ssl(server, SslMode.DISABLE), target.password).test()
    assert check.ok, check.failure
    assert next(step for step in check.steps if step.name == "TLS").detail == "not encrypted"


def test_a_server_certificate_signed_by_the_given_ca_is_accepted(
    target: Target, server: ServerConnection, pki: dict[str, str]
) -> None:
    for mode in (SslMode.VERIFY_CA, SslMode.VERIFY_FULL):
        config = with_ssl(server, mode, ca_file=pki["ca.pem"])
        check = create_client(config, target.password).test()
        assert check.ok, (mode, check.failure)
        assert any(step.name == "TLS files" for step in check.steps)


def test_a_certificate_of_another_ca_is_refused(
    target: Target, server: ServerConnection, pki: dict[str, str]
) -> None:
    config = with_ssl(server, SslMode.VERIFY_CA, ca_file=pki["other-ca.pem"])
    check = create_client(config, target.password).test()
    assert not check.ok
    assert isinstance(check.failure, SslError)
    assert not any(step.name == "TLS" for step in check.steps)


def test_verify_full_checks_the_host_name_even_through_a_route(
    target: Target, server: ServerConnection, pki: dict[str, str]
) -> None:
    """The name in the config is what the certificate must be issued for; the route is only
    where the socket goes (that is how SSH tunnels work)."""
    port = server.effective_port
    assert port is not None
    route = ConnectRuntime(route=Route("127.0.0.1", port))
    config = with_ssl(server, SslMode.VERIFY_FULL, ca_file=pki["ca.pem"])
    named = replace(config, host="localhost")
    assert isinstance(named, ServerConnection)
    assert create_client(named, target.password, route).test().ok  # in the certificate
    stranger = replace(config, host="db.example.test")
    assert isinstance(stranger, ServerConnection)
    check = create_client(stranger, target.password, route).test()
    assert not check.ok
    assert isinstance(check.failure, SslError)
    # verify-ca does not care about names
    relaxed = replace(stranger, ssl=SslConfig(mode=SslMode.VERIFY_CA, ca_file=pki["ca.pem"]))
    assert isinstance(relaxed, ServerConnection)
    assert create_client(relaxed, target.password, route).test().ok


def test_a_client_certificate_logs_in_without_a_password(
    target: Target, server: ServerConnection, pki: dict[str, str]
) -> None:
    ssl = SslConfig(
        mode=SslMode.VERIFY_CA,
        ca_file=pki["ca.pem"],
        cert_file=pki["client.pem"],
        key_file=pki["client.key"],
    )
    with create_client(as_cert_user(server, ssl), None) as client:
        assert client.execute("SELECT 1").rows == ((1,),)
    check = create_client(as_cert_user(server, ssl), None).test()
    assert [s.name for s in check.steps if s.name.startswith("TLS")] == ["TLS files", "TLS"]


def test_without_the_certificate_the_server_says_no(
    target: Target, server: ServerConnection, pki: dict[str, str]
) -> None:
    ssl = SslConfig(mode=SslMode.VERIFY_CA, ca_file=pki["ca.pem"])
    with pytest.raises(ConnectionFailed):
        create_client(as_cert_user(server, ssl), None).connect()


def test_an_encrypted_client_key_needs_its_passphrase(
    target: Target, server: ServerConnection, pki: dict[str, str]
) -> None:
    ssl = SslConfig(
        mode=SslMode.VERIFY_CA,
        ca_file=pki["ca.pem"],
        cert_file=pki["client.pem"],
        key_file=pki["client-encrypted.key"],
    )
    config = as_cert_user(server, ssl)
    check = create_client(config, None).test()
    assert isinstance(check.failure, SslFileError)
    assert check.steps[-1].name == "TLS files"
    assert not check.steps[-1].ok
    runtime = ConnectRuntime(ssl_key_password=PASSPHRASE)
    with create_client(config, None, runtime) as client:
        assert client.execute("SELECT 1").rows == ((1,),)
    wrong = create_client(config, None, ConnectRuntime(ssl_key_password="nope")).test()
    assert isinstance(wrong.failure, SslFileError)

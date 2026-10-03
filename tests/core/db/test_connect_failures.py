"""How connection problems are classified, and what ``test()`` reports for them."""

from __future__ import annotations

import socket
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from easydbms.core.connections import FileConnection, ServerConnection, replace
from easydbms.core.db import (
    AuthFailed,
    ConnectionFailed,
    ConnectTimeout,
    DatabaseFileError,
    DatabaseNotFound,
    DnsError,
    InvalidOptionError,
    PortClosed,
    SslError,
    create_client,
)
from easydbms.core.db.diagnostics import network_steps
from tests.core.conftest import UNSET, Target, Unset


def server_target(target: Target) -> ServerConnection:
    if not isinstance(target.config, ServerConnection):
        pytest.skip("server connections only")
    return target.config


def connect_error(
    target: Target, config: Any, password: str | Unset | None = UNSET
) -> ConnectionFailed:
    client = target.client(config, password)
    with pytest.raises(ConnectionFailed) as info:
        client.connect()
    assert not client.is_connected
    return info.value


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------- servers


def test_wrong_password_and_wrong_user(target: Target) -> None:
    config = server_target(target)
    assert isinstance(connect_error(target, config, "definitely-wrong"), AuthFailed)
    assert isinstance(connect_error(target, replace(config, user="no_such_user_xyz")), AuthFailed)


def test_unknown_database(target: Target) -> None:
    config = server_target(target)
    error = connect_error(target, replace(config, database="no_such_database_xyz"))
    # PostgreSQL says so explicitly; MySQL/MariaDB may report a missing grant instead
    assert isinstance(error, DatabaseNotFound | AuthFailed)
    if target.name == "postgresql":
        assert isinstance(error, DatabaseNotFound)


def test_closed_port(target: Target) -> None:
    config = server_target(target)
    assert isinstance(connect_error(target, replace(config, host="127.0.0.1", port=1)), PortClosed)


def test_unresolvable_host(target: Target) -> None:
    config = server_target(target)
    error = connect_error(target, replace(config, host="no-such-host.invalid"))
    assert isinstance(error, DnsError)


def test_tls_problems(target: Target) -> None:
    config = server_target(target)
    if target.name == "postgresql":
        options = {"sslmode": "verify-full"}
    else:
        options = {"ssl_verify_cert": "true", "ssl_ca": "/etc/ssl/certs/ca-certificates.crt"}
    assert isinstance(connect_error(target, replace(config, options=options)), SslError)
    if target.name == "mysql":
        error = connect_error(target, replace(config, options={"ssl_ca": "/no/such/ca.pem"}))
        assert isinstance(error, SslError)


def test_invalid_options(target: Target) -> None:
    config = server_target(target)
    assert isinstance(
        connect_error(target, replace(config, options={"bogus": "1"})), InvalidOptionError
    )
    if target.name == "mysql":
        error = connect_error(target, replace(config, options={"connect_timeout": "soon"}))
        assert isinstance(error, InvalidOptionError)
        assert "whole number" in str(error)
        error = connect_error(target, replace(config, options={"ssl_disabled": "maybe"}))
        assert "true or false" in str(error)


def test_options_reach_the_driver_and_a_good_connection_still_works_afterwards(
    target: Target,
) -> None:
    config = server_target(target)
    options = {"connect_timeout": "5"}
    with target.client(replace(config, options=options)) as client:
        assert client.execute("SELECT 1").rows == ((1,),)


# ---------------------------------------------------------------------------- SQLite files


def file_client(path: Path, **extra: Any) -> Any:
    config = FileConnection.model_validate({"name": "f", "path": str(path), **extra})
    return create_client(config)


def test_sqlite_missing_file_is_not_silently_created(tmp_path: Path) -> None:
    missing = tmp_path / "typo.db"
    client = file_client(missing)
    with pytest.raises(DatabaseFileError, match="does not exist"):
        client.connect()
    assert not missing.exists()


def test_sqlite_create_if_missing(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    with file_client(path, create_if_missing=True) as client:
        client.execute("CREATE TABLE t (a integer)")
    assert path.exists()
    with pytest.raises(DatabaseFileError, match="folder"):
        file_client(tmp_path / "no" / "such" / "dir.db", create_if_missing=True).connect()
    # read-only never creates
    with pytest.raises(DatabaseFileError, match="does not exist"):
        file_client(tmp_path / "ro.db", create_if_missing=True, read_only=True).connect()


def test_sqlite_directory_and_garbage_files(tmp_path: Path) -> None:
    with pytest.raises(DatabaseFileError, match="directory"):
        file_client(tmp_path).connect()
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is definitely not a sqlite database file" * 10)
    with pytest.raises(DatabaseFileError, match="not a SQLite database"):
        file_client(garbage).connect()


def test_sqlite_home_directory_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sqlite3.connect(tmp_path / "home.db").close()
    with file_client(Path("~/home.db")) as client:
        assert client.execute("SELECT 1").rows == ((1,),)


def test_sqlite_read_only_file_cannot_be_written(tmp_path: Path) -> None:
    path = tmp_path / "ro.db"
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE t (a integer)")
    seed.commit()
    seed.close()
    with file_client(path, read_only=True) as client:
        assert client.execute("SELECT count(*) FROM t").rows == ((0,),)
        with pytest.raises(Exception, match=r"readonly"):
            client.execute("INSERT INTO t VALUES (1)")


# ---------------------------------------------------------------------------- test() report


def test_successful_test_report(target: Target) -> None:
    client = target.client()
    check = client.test()
    assert check.ok
    assert check.failure is None
    assert all(step.ok for step in check.steps)
    names = [step.name for step in check.steps]
    assert names == (
        ["DNS", "TCP", "Login", "TLS", "Query"] if target.is_server else ["File", "Open", "Query"]
    )
    assert check.server_version
    assert check.server_version[0].isdigit()
    assert not client.is_connected  # test() uses a throw-away connection


def test_report_stops_at_wrong_password(target: Target) -> None:
    config = server_target(target)
    check = target.client(config, "nope-nope").test()
    assert not check.ok
    assert isinstance(check.failure, AuthFailed)
    assert [(s.name, s.ok) for s in check.steps] == [("DNS", True), ("TCP", True), ("Login", False)]
    assert check.server_version is None


def test_report_stops_at_closed_port(target: Target) -> None:
    config = server_target(target)
    check = target.client(replace(config, host="127.0.0.1", port=1)).test()
    assert isinstance(check.failure, PortClosed)
    assert [(s.name, s.ok) for s in check.steps] == [("DNS", True), ("TCP", False)]


def test_report_stops_at_dns(target: Target) -> None:
    config = server_target(target)
    check = target.client(replace(config, host="no-such-host.invalid")).test()
    assert isinstance(check.failure, DnsError)
    assert [(s.name, s.ok) for s in check.steps] == [("DNS", False)]
    assert "no-such-host.invalid" in check.steps[0].detail


def test_report_for_a_missing_sqlite_file(tmp_path: Path) -> None:
    check = file_client(tmp_path / "missing.db").test()
    assert isinstance(check.failure, DatabaseFileError)
    assert [(s.name, s.ok) for s in check.steps] == [("File", False)]


def test_report_for_a_garbage_sqlite_file(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database " * 50)
    check = file_client(garbage).test()
    assert [(s.name, s.ok) for s in check.steps] == [("File", True), ("Open", False)]
    assert isinstance(check.failure, DatabaseFileError)


# ---------------------------------------------------------------------------- network steps (unit)


def test_network_steps_are_skipped_for_unix_sockets_and_unknown_ports() -> None:
    assert network_steps("/var/run/postgresql", 5432) == []
    assert network_steps("localhost", None) == []


def test_tcp_timeout_is_reported_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(self: socket.socket, address: Any) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(socket.socket, "connect", hang)
    dns, tcp = (action for _, action in network_steps("127.0.0.1", 5432, timeout=0.5))
    dns()
    with pytest.raises(ConnectTimeout, match=r"did not answer within 0\.5s"):
        tcp()

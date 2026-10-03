from __future__ import annotations

from pathlib import Path

import pytest

from easydbms.core.connections import (
    ServerConnection,
    SslMode,
    complete,
)

PROD = """
[prod]
host=db.prod
port=6432
dbname=sales
user=app
password=service-secret
sslmode=verify-full
sslrootcert=/etc/ca.pem
sslcert=/etc/c.pem
sslkey=/etc/c.key
sslpassword=key-secret
application_name=easydbms
connect_timeout=7
"""


def config(**overrides: object) -> ServerConnection:
    data: dict[str, object] = {"name": "x", "dialect": "postgresql", "host": ""} | overrides
    return ServerConnection.model_validate(data)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    service = tmp_path / "svc.conf"
    service.write_text(PROD)
    return {
        "PGSERVICEFILE": str(service),
        "PGSYSCONFDIR": str(tmp_path / "none"),
        "PGPASSFILE": str(tmp_path / "pgpass"),
    }


def test_a_service_fills_every_empty_field(env: dict[str, str]) -> None:
    done = complete(config(service="prod"), None, None, env)
    assert (done.config.host, done.config.port, done.config.database) == ("db.prod", 6432, "sales")
    assert done.config.user == "app"
    assert done.config.ssl.mode is SslMode.VERIFY_FULL
    assert (done.config.ssl.ca_file, done.config.ssl.cert_file, done.config.ssl.key_file) == (
        "/etc/ca.pem",
        "/etc/c.pem",
        "/etc/c.key",
    )
    assert done.config.options == {"application_name": "easydbms", "connect_timeout": "7"}
    assert (done.password, done.ssl_key_password) == ("service-secret", "key-secret")
    assert [name for name, _ in done.notes] == ["Service", "Password"]
    assert "prod" in done.notes[0][1]


def test_what_the_connection_says_wins_over_the_service(env: dict[str, str]) -> None:
    mine = config(
        service="prod",
        host="mine",
        port=1,
        user="me",
        database="db",
        ssl={"mode": "require"},
        options={"connect_timeout": "1"},
    )
    done = complete(mine, "typed", None, env)
    assert (done.config.host, done.config.port, done.config.user) == ("mine", 1, "me")
    assert done.config.database == "db"
    assert done.config.ssl.mode is SslMode.REQUIRE
    assert done.config.options["connect_timeout"] == "1"
    assert done.password == "typed"  # a typed or saved password beats the service's
    assert [name for name, _ in done.notes] == ["Service"]


def test_pgpass_comes_before_the_service_password(env: dict[str, str], tmp_path: Path) -> None:
    pgpass = tmp_path / "pgpass"
    pgpass.write_text("db.prod:6432:sales:app:from-pgpass\n")
    pgpass.chmod(0o600)
    done = complete(config(service="prod"), None, None, env)
    assert done.password == "from-pgpass"
    assert done.notes[-1][0] == "Password"
    assert "line 1" in done.notes[-1][1]


def test_pgpass_alone_works_without_a_service(env: dict[str, str], tmp_path: Path) -> None:
    pgpass = tmp_path / "pgpass"
    pgpass.write_text("h:5432:*:u:pw\n")
    pgpass.chmod(0o600)
    done = complete(config(host="h", user="u"), None, None, env)
    assert done.password == "pw"
    assert complete(config(host="h", user="other"), None, None, env).password is None


def test_an_unsafe_pgpass_is_reported_not_used(env: dict[str, str], tmp_path: Path) -> None:
    pgpass = tmp_path / "pgpass"
    pgpass.write_text("h:5432:*:u:pw\n")
    pgpass.chmod(0o644)
    done = complete(config(host="h", user="u"), None, None, env)
    assert done.password is None
    assert "chmod 0600" in done.pgpass_problem


def test_a_database_less_connection_is_looked_up_by_user_name(
    env: dict[str, str], tmp_path: Path
) -> None:
    pgpass = tmp_path / "pgpass"
    pgpass.write_text("h:5432:u:u:pw\n")  # libpq connects to the database named like the user
    pgpass.chmod(0o600)
    assert complete(config(host="h", user="u"), None, None, env).password == "pw"


def test_mysql_connections_are_left_alone(env: dict[str, str]) -> None:
    mysql = ServerConnection.model_validate({"name": "m", "dialect": "mysql", "host": "h"})
    done = complete(mysql, None, None, env)
    assert done.config is mysql
    assert done.password is None
    assert done.notes == ()

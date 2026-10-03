from __future__ import annotations

import pytest

from sql_erd_studio.core.connections import (
    ConnectionUrlError,
    FileConnection,
    ServerConnection,
    build_connection_url,
    parse_connection_url,
)


def server_of(url: str) -> ServerConnection:
    config = parse_connection_url(url).config
    assert isinstance(config, ServerConnection)
    return config


def file_of(url: str) -> FileConnection:
    config = parse_connection_url(url).config
    assert isinstance(config, FileConnection)
    return config


def test_full_postgresql_url() -> None:
    parsed = parse_connection_url(
        "postgresql://app:s3cret@db.example.com:6543/shop?sslmode=require"
    )
    config = parsed.config
    assert isinstance(config, ServerConnection)
    assert (config.dialect.value, config.host, config.port) == (
        "postgresql",
        "db.example.com",
        6543,
    )
    assert (config.user, config.database) == ("app", "shop")
    assert config.options == {"sslmode": "require"}
    assert parsed.password == "s3cret"
    assert config.name == "app@shop"


@pytest.mark.parametrize(
    ("url", "dialect"),
    [
        ("postgres://u@h/d", "postgresql"),
        ("postgresql+psycopg://u@h/d", "postgresql"),
        ("mysql://u@h/d", "mysql"),
        ("mariadb://u@h/d", "mysql"),
        ("mysql+pymysql://u@h/d", "mysql"),
    ],
)
def test_scheme_aliases(url: str, dialect: str) -> None:
    assert server_of(url).dialect.value == dialect


def test_minimal_urls_use_defaults() -> None:
    config = server_of("postgresql://localhost")
    assert (config.host, config.port, config.user, config.database) == ("localhost", None, "", "")
    assert config.effective_port == 5432
    assert parse_connection_url("mysql://u@h/d").password is None
    assert server_of("postgresql:///mydb").host == "localhost"


def test_credentials_are_percent_decoded() -> None:
    parsed = parse_connection_url("postgresql://us%40er:p%40ss%3Aw%2Ford%25@h/my%20db")
    assert isinstance(parsed.config, ServerConnection)
    assert parsed.config.user == "us@er"
    assert parsed.config.database == "my db"
    assert parsed.password == "p@ss:w/ord%"


def test_empty_password_is_distinct_from_no_password() -> None:
    assert parse_connection_url("postgresql://u:@h/d").password == ""
    assert parse_connection_url("postgresql://u@h/d").password is None


def test_ipv6_hosts() -> None:
    config = server_of("postgresql://u@[::1]:5433/d")
    assert (config.host, config.port) == ("::1", 5433)
    assert build_connection_url(config) == "postgresql://u@[::1]:5433/d"


def test_custom_name_and_url_whitespace() -> None:
    parsed = parse_connection_url("  postgresql://u@h/d\n", name="Production")
    assert parsed.config.name == "Production"


@pytest.mark.parametrize(
    ("url", "path", "read_only"),
    [
        ("sqlite:///relative/x.db", "relative/x.db", False),
        ("sqlite:////abs/x.db", "/abs/x.db", False),
        ("sqlite:///C:/Users/me/x.db", "C:/Users/me/x.db", False),
        ("sqlite:///my%20file.db", "my file.db", False),
        ("sqlite://", ":memory:", False),
        ("sqlite:///:memory:", ":memory:", False),
        ("sqlite:////abs/x.db?mode=ro", "/abs/x.db", True),
    ],
)
def test_sqlite_urls(url: str, path: str, read_only: bool) -> None:
    config = file_of(url)
    assert (config.path, config.read_only) == (path, read_only)


def test_sqlite_default_names() -> None:
    assert file_of("sqlite:////home/me/data/shop.db").name == "shop.db"
    assert file_of("sqlite://").name == "In-memory database"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("not-a-url", "no scheme"),
        ("mssql://sa@h/d", "unsupported"),
        ("oracle://u@h/d", "unsupported"),
        ("postgresql://u@h:notaport/d", "port"),
        ("postgresql://u@h:70000/d", "port"),
        ("postgresql://u@h1,h2/d", "several hosts"),
        ("sqlite://host/x.db", "no host"),
        ("sqlite:///x.db?cache=shared", "unsupported SQLite URL option"),
    ],
)
def test_invalid_urls(url: str, message: str) -> None:
    with pytest.raises(ConnectionUrlError, match=message):
        parse_connection_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://app@db.example.com:6543/shop?sslmode=require",
        "postgresql://localhost",
        "postgresql://u@h/d?application_name=a%20b&connect_timeout=5",
        "mysql://root@127.0.0.1:3307/",
        "mysql://us%40er@h/my%20db",
        "sqlite:///relative/x.db",
        "sqlite:////abs/path/with%20space.db?mode=ro",
        "sqlite:///C:/x.db",
        "sqlite://",
    ],
)
def test_build_then_parse_round_trips(url: str) -> None:
    config = parse_connection_url(url).config
    rebuilt = build_connection_url(config)
    again = parse_connection_url(rebuilt).config
    assert again.model_dump(exclude={"id"}) == config.model_dump(exclude={"id"})


def test_built_urls_never_contain_a_password() -> None:
    parsed = parse_connection_url("postgresql://app:hunter2@h/d")
    assert "hunter2" not in build_connection_url(parsed.config)
    assert build_connection_url(parsed.config) == "postgresql://app@h/d"


def test_build_normalises_to_the_canonical_scheme() -> None:
    assert build_connection_url(server_of("postgres://u@h/d")) == "postgresql://u@h/d"
    assert build_connection_url(server_of("mariadb://u@h/d")) == "mysql://u@h/d"

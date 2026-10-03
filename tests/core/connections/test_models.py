from __future__ import annotations

import pytest
from pydantic import ValidationError

from easydbms.core.connections import (
    ConnectionColor,
    FileConnection,
    ServerConnection,
    parse_config,
    replace,
)
from easydbms.core.dialects import MYSQL, POSTGRESQL, SQLITE


def server(**overrides: object) -> ServerConnection:
    data: dict[str, object] = {"name": "pg", "dialect": "postgresql", "host": "db.local"}
    return ServerConnection.model_validate(data | overrides)


def test_discriminated_union_picks_the_class_by_kind() -> None:
    assert isinstance(
        parse_config({"kind": "server", "name": "a", "dialect": "mysql"}), ServerConnection
    )
    config = parse_config({"kind": "file", "name": "f", "path": "/tmp/x.db"})
    assert isinstance(config, FileConnection)
    assert config.dialect_impl is SQLITE
    with pytest.raises(ValidationError):
        parse_config({"kind": "carrier-pigeon", "name": "x"})


def test_server_connections_are_limited_to_postgresql_and_mysql() -> None:
    assert server(dialect="mysql").dialect_impl is MYSQL
    assert server().dialect_impl is POSTGRESQL
    for unsupported in ("sqlite", "mssql", "oracle"):
        with pytest.raises(ValidationError):
            server(dialect=unsupported)


def test_ids_are_unique_and_names_are_required() -> None:
    assert server().id != server().id
    with pytest.raises(ValidationError):
        server(name="")
    with pytest.raises(ValidationError):
        server(name="   ")


@pytest.mark.parametrize("port", [0, -1, 65536, 99999])
def test_port_range(port: int) -> None:
    with pytest.raises(ValidationError):
        server(port=port)


def test_unknown_fields_are_rejected_so_a_password_cannot_sneak_into_the_file() -> None:
    with pytest.raises(ValidationError):
        server(password="secret")


def test_effective_port_and_subtitle() -> None:
    assert server().effective_port == 5432
    assert server(dialect="mysql").effective_port == 3306
    assert server(port=6543).effective_port == 6543
    assert server(user="app", database="shop", port=6543).subtitle == "app@db.local:6543/shop"
    assert server().subtitle == "db.local"
    assert FileConnection(name="f", path="/data/x.db").subtitle == "/data/x.db"


def test_production_connections_are_red_unless_coloured() -> None:
    assert server().display_color is None
    assert server(production=True).display_color is ConnectionColor.RED
    assert server(production=True, color="blue").display_color is ConnectionColor.BLUE
    assert server(color="green").display_color is ConnectionColor.GREEN


def test_option_names_must_not_be_blank() -> None:
    assert server(options={"sslmode": "require"}).options == {"sslmode": "require"}
    with pytest.raises(ValidationError):
        server(options={" ": "x"})


def test_replace_validates() -> None:
    original = server()
    changed = replace(original, host="other", port=1234)
    assert (changed.id, changed.name) == (original.id, original.name)
    assert isinstance(changed, ServerConnection)
    assert (changed.host, changed.port) == ("other", 1234)
    with pytest.raises(ValidationError):
        replace(original, port=0)
    assert original.host == "db.local"


def test_json_round_trip_keeps_the_union_intact() -> None:
    configs = [
        server(options={"sslmode": "require"}),
        FileConnection(name="f", path="x.db", read_only=True),
    ]
    for config in configs:
        again = parse_config(config.model_dump(mode="json"))
        assert again == config
        assert type(again) is type(config)

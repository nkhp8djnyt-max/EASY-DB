from __future__ import annotations

import pytest

from sql_erd_studio.core.connections import (
    FileConnection,
    ServerConnection,
    UnresolvedVariableError,
    expand,
    has_placeholders,
    resolve_config,
)

ENV = {"DB_HOST": "prod.example.com", "DB_USER": "app", "EMPTY": "", "PW": "s3cret"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("${DB_HOST}", "prod.example.com"),
        ("pre-${DB_USER}-post", "pre-app-post"),
        ("${DB_HOST}:${DB_USER}", "prod.example.com:app"),
        ("${MISSING:-fallback}", "fallback"),
        ("${EMPTY:-fallback}", "fallback"),
        ("${DB_HOST:-fallback}", "prod.example.com"),
        ("${MISSING:-}", ""),
        ("no placeholders", "no placeholders"),
        ("$${DB_HOST}", "${DB_HOST}"),
        ("cost: $5 and $HOME", "cost: $5 and $HOME"),
    ],
)
def test_expand(text: str, expected: str) -> None:
    assert expand(text, ENV) == expected


def test_undefined_variables_are_reported_together() -> None:
    with pytest.raises(UnresolvedVariableError) as info:
        expand("${A}-${B}-${A}", ENV)
    assert info.value.names == ("A", "B")
    assert "A, B" in str(info.value)


def test_has_placeholders_ignores_escapes() -> None:
    assert has_placeholders("x${A}y")
    assert not has_placeholders("x$${A}y")
    assert not has_placeholders("plain")


def test_resolve_server_config_and_password() -> None:
    config = ServerConnection.model_validate(
        {
            "name": "p",
            "dialect": "postgresql",
            "host": "${DB_HOST}",
            "user": "${DB_USER}",
            "database": "${DB_NAME:-shop}",
            "port": 5433,
            "options": {"application_name": "${DB_USER}-app"},
        }
    )
    resolved, password = resolve_config(config, "${PW}", ENV)
    assert isinstance(resolved, ServerConnection)
    assert (resolved.host, resolved.user, resolved.database) == ("prod.example.com", "app", "shop")
    assert resolved.options == {"application_name": "app-app"}
    assert resolved.port == 5433
    assert password == "s3cret"
    assert config.host == "${DB_HOST}"  # the saved config is untouched


def test_resolve_file_config_and_missing_password() -> None:
    config = FileConnection(name="f", path="${DATA:-/var/data}/x.db")
    resolved, password = resolve_config(config, None, ENV)
    assert isinstance(resolved, FileConnection)
    assert resolved.path == "/var/data/x.db"
    assert password is None


def test_resolve_reports_every_missing_variable_across_fields() -> None:
    config = ServerConnection.model_validate(
        {"name": "p", "dialect": "mysql", "host": "${H}", "user": "${U}"}
    )
    with pytest.raises(UnresolvedVariableError) as info:
        resolve_config(config, "${P}", {})
    assert info.value.names == ("H", "U", "P")

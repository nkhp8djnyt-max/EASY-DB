from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from easydbms.core.connections import (
    ALL_FIELDS,
    JUMP_PASSPHRASE,
    JUMP_PASSWORD,
    PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SSL_KEY_PASSWORD,
    ConnectionStore,
    MemorySecretStore,
    ServerConnection,
    SshAuth,
    SshConfig,
    SshHop,
    SslConfig,
    SslMode,
    build_connection_url,
    parse_connection_url,
    resolve_config,
)
from easydbms.core.connections.models import ssl_from_options, ssl_to_options
from easydbms.core.dialects import DialectId


def server(**overrides: Any) -> ServerConnection:
    data: dict[str, Any] = {"name": "pg", "dialect": "postgresql", "host": "db.local"}
    return ServerConnection.model_validate(data | overrides)


# ---------------------------------------------------------------------------- TLS settings


def test_tls_options_of_an_old_file_become_settings_of_their_own() -> None:
    config = server(
        options={
            "sslmode": "verify-full",
            "sslrootcert": "/ca.pem",
            "sslcert": "/c.pem",
            "sslkey": "/k.pem",
            "connect_timeout": "5",
        }
    )
    assert config.ssl == SslConfig(
        mode=SslMode.VERIFY_FULL, ca_file="/ca.pem", cert_file="/c.pem", key_file="/k.pem"
    )
    assert config.options == {"connect_timeout": "5"}


def test_an_explicit_ssl_block_wins_over_options() -> None:
    config = server(options={"sslmode": "require"}, ssl={"mode": "verify-ca", "ca_file": "/x"})
    assert config.ssl.mode is SslMode.VERIFY_CA
    assert config.ssl.ca_file == "/x"


@pytest.mark.parametrize(
    ("options", "mode"),
    [
        ({"ssl_disabled": "true"}, SslMode.DISABLE),
        ({"ssl_mode": "REQUIRED"}, SslMode.REQUIRE),
        ({"ssl_mode": "VERIFY_IDENTITY"}, SslMode.VERIFY_FULL),
        ({"ssl_verify_cert": "1"}, SslMode.VERIFY_CA),
        ({"ssl_verify_identity": "yes"}, SslMode.VERIFY_FULL),
        ({"ssl_ca": "/ca.pem"}, SslMode.REQUIRE),  # PyMySQL: files switch TLS on
    ],
)
def test_mysql_tls_options_map_to_modes(options: dict[str, str], mode: SslMode) -> None:
    ssl, rest = ssl_from_options(DialectId.MYSQL, options)
    assert ssl.mode is mode
    assert rest == {}


def test_tls_settings_round_trip_through_url_parameters() -> None:
    for dialect in (DialectId.POSTGRESQL, DialectId.MYSQL):
        ssl = SslConfig(
            mode=SslMode.VERIFY_FULL, ca_file="/ca.pem", cert_file="/c.pem", key_file="/k.pem"
        )
        again, rest = ssl_from_options(dialect, ssl_to_options(dialect, ssl))
        assert again == ssl
        assert rest == {}


def test_a_url_keeps_the_tls_settings_and_never_a_password() -> None:
    config = server(user="u", database="d", ssl={"mode": "verify-ca", "ca_file": "/ca.pem"})
    url = build_connection_url(config)
    assert "sslmode=verify-ca" in url
    assert "sslrootcert=%2Fca.pem" in url
    parsed = parse_connection_url(url).config
    assert isinstance(parsed, ServerConnection)
    assert parsed.ssl == config.ssl


def test_ssl_properties() -> None:
    assert SslConfig().is_default
    assert not SslConfig(mode=SslMode.DISABLE).is_default
    assert SslConfig(mode=SslMode.REQUIRE).encrypts
    assert not SslConfig(mode=SslMode.PREFER).encrypts
    assert SslMode.VERIFY_CA.verifies
    assert not SslMode.REQUIRE.verifies


def test_unknown_ssl_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SslConfig.model_validate({"mode": "require", "password": "no"})


# ---------------------------------------------------------------------------- SSH settings


def test_a_key_login_needs_a_key_file() -> None:
    with pytest.raises(ValidationError, match="private key file"):
        SshHop(host="h", auth=SshAuth.KEY)
    assert SshHop(host="h", auth=SshAuth.KEY, key_file="~/.ssh/id_ed25519").port == 22


def test_ssh_ports_are_validated_and_secrets_cannot_be_stored() -> None:
    with pytest.raises(ValidationError):
        SshHop(host="h", port=0)
    with pytest.raises(ValidationError):
        SshHop.model_validate({"host": "h", "password": "x"})
    with pytest.raises(ValidationError):
        SshHop(host="")


def test_ssh_settings_survive_the_json_file(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "c.json")
    ssh = SshConfig(
        server=SshHop(host="inner", user="u", auth=SshAuth.KEY, key_file="/k"),
        jump=SshHop(host="bastion", port=2222, user="j", auth=SshAuth.PASSWORD),
    )
    config = server(ssh=ssh.model_dump(), service="prod", ssl={"mode": "require"})
    store.save(config)
    text = (tmp_path / "c.json").read_text()
    assert "bastion" in text
    assert "passphrase" not in text
    assert "wonderland" not in text
    loaded = ConnectionStore(tmp_path / "c.json").get(config.id)
    assert loaded == config


def test_the_subtitle_mentions_the_tunnel_and_the_service() -> None:
    assert "SSH bastion" in server(ssh={"server": {"host": "bastion"}}).subtitle
    assert "service:prod" in server(host="", service="prod").subtitle


def test_a_service_can_stand_in_for_the_host() -> None:
    assert server(host="", service="prod").service == "prod"
    with pytest.raises(ValidationError, match="host name"):
        server(host="  ")


def test_placeholders_are_expanded_in_files_hosts_and_the_service() -> None:
    config = server(
        host="",
        service="${SVC}",
        ssl={"mode": "verify-ca", "ca_file": "${PKI}/ca.pem"},
        ssh={
            "server": {"host": "${BASTION}", "user": "${ME}", "auth": "key", "key_file": "${K}"},
            "jump": {"host": "j-${BASTION}"},
        },
    )
    env = {"SVC": "prod", "PKI": "/pki", "BASTION": "b.example", "ME": "me", "K": "/k"}
    resolved, _ = resolve_config(config, None, env)
    assert isinstance(resolved, ServerConnection)
    assert resolved.service == "prod"
    assert resolved.ssl.ca_file == "/pki/ca.pem"
    assert resolved.ssh is not None
    assert (resolved.ssh.server.host, resolved.ssh.server.user) == ("b.example", "me")
    assert resolved.ssh.server.key_file == "/k"
    assert resolved.ssh.jump is not None
    assert resolved.ssh.jump.host == "j-b.example"
    with pytest.raises(ValueError, match="BASTION"):
        resolve_config(config, None, {"SVC": "x"})


# ---------------------------------------------------------------------------- secrets


def test_every_secret_field_is_forgotten_with_the_connection() -> None:
    store = MemorySecretStore()
    for field in ALL_FIELDS:
        store.set("c1", f"value-{field}", field)
        store.set("c2", "other", field)
    assert {PASSWORD, SSH_PASSWORD, SSH_PASSPHRASE, JUMP_PASSWORD, JUMP_PASSPHRASE} <= set(
        ALL_FIELDS
    )
    assert SSL_KEY_PASSWORD in ALL_FIELDS
    assert store.get("c1", SSH_PASSPHRASE) == f"value-{SSH_PASSPHRASE}"
    store.delete_all("c1")
    assert all(store.get("c1", field) is None for field in ALL_FIELDS)
    assert all(store.get("c2", field) == "other" for field in ALL_FIELDS)

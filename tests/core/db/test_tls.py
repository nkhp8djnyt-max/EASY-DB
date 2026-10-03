"""TLS files and settings, without a server: what is checked, what each driver is given."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from easydbms.core.connections import SslConfig, SslMode
from easydbms.core.db import SslFileError
from easydbms.core.db.tls import (
    check_files,
    file_steps,
    key_is_encrypted,
    mysql_ssl,
    postgres_params,
)
from tests.support.certs import PASSPHRASE, key_pem, make_ca, make_leaf, write_pki


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return write_pki(tmp_path_factory.mktemp("pki"))


def files(pki: dict[str, Path], *, key: str = "client.key") -> SslConfig:
    return SslConfig(
        mode=SslMode.VERIFY_FULL,
        ca_file=str(pki["ca.pem"]),
        cert_file=str(pki["client.pem"]),
        key_file=str(pki[key]),
    )


def test_good_files_are_described(pki: dict[str, Path]) -> None:
    checks = check_files(files(pki))
    assert [c.label for c in checks] == ["CA certificate", "client certificate", "client key"]
    assert "EasyDBMS test CA" in checks[0].detail
    assert "erd_cert" in checks[1].detail
    assert "valid until" in checks[1].detail
    assert checks[2].detail == "matches the certificate"


def test_a_missing_file_is_named(pki: dict[str, Path]) -> None:
    with pytest.raises(SslFileError, match=r"CA certificate file '/no/such/ca.pem' does not exist"):
        check_files(SslConfig(ca_file="/no/such/ca.pem"))


def test_a_file_that_is_not_a_certificate_is_rejected(pki: dict[str, Path]) -> None:
    with pytest.raises(SslFileError, match="holds no PEM certificate"):
        check_files(SslConfig(ca_file=str(pki["client.key"])))


def test_an_expired_certificate_is_reported_with_its_date(tmp_path: Path) -> None:
    ca = make_ca()
    expired, key = make_leaf(ca, "old", client=True, valid_days=-5)
    from tests.support.certs import certificate_pem

    (tmp_path / "old.pem").write_bytes(certificate_pem(expired))
    (tmp_path / "old.key").write_bytes(key_pem(key))
    config = SslConfig(cert_file=str(tmp_path / "old.pem"), key_file=str(tmp_path / "old.key"))
    with pytest.raises(SslFileError, match=r"'old' expired on \d{4}-\d\d-\d\d"):
        check_files(config)


def test_a_key_of_another_certificate_is_caught(pki: dict[str, Path], tmp_path: Path) -> None:
    stranger = make_leaf(make_ca(), "x")[1]
    (tmp_path / "other.key").write_bytes(key_pem(stranger))
    config = files(pki)
    config = config.model_copy(update={"key_file": str(tmp_path / "other.key")})
    with pytest.raises(SslFileError, match="does not belong to the certificate"):
        check_files(config)


def test_an_encrypted_key_needs_its_passphrase(pki: dict[str, Path]) -> None:
    config = files(pki, key="client-encrypted.key")
    assert key_is_encrypted(str(pki["client-encrypted.key"]))
    assert not key_is_encrypted(str(pki["client.key"]))
    with pytest.raises(SslFileError, match="passphrase is needed"):
        check_files(config)
    with pytest.raises(SslFileError, match="wrong passphrase"):
        check_files(config, "nope")
    assert check_files(config, PASSPHRASE)[-1].label == "client key"


def test_a_key_without_a_certificate_is_an_error(pki: dict[str, Path]) -> None:
    with pytest.raises(SslFileError, match="needs its client certificate"):
        check_files(SslConfig(key_file=str(pki["client.key"])))


def test_the_key_may_live_in_the_certificate_file(pki: dict[str, Path], tmp_path: Path) -> None:
    both = tmp_path / "both.pem"
    both.write_bytes(pki["client.pem"].read_bytes() + pki["client.key"].read_bytes())
    assert check_files(SslConfig(cert_file=str(both)))[-1].path == str(both)


def test_file_steps_exist_only_when_files_are_configured(pki: dict[str, Path]) -> None:
    assert file_steps(SslConfig(mode=SslMode.REQUIRE)) == []
    assert file_steps(SslConfig(mode=SslMode.DISABLE, ca_file=str(pki["ca.pem"]))) == []
    ((name, run),) = file_steps(files(pki))
    assert name == "TLS files"
    assert "CA certificate" in run()
    assert "client key" in run()


def test_libpq_parameters(pki: dict[str, Path]) -> None:
    assert postgres_params(SslConfig()) == {}
    assert postgres_params(SslConfig(mode=SslMode.REQUIRE)) == {"sslmode": "require"}
    params = postgres_params(files(pki), "pw")
    assert params == {
        "sslmode": "verify-full",
        "sslrootcert": str(pki["ca.pem"]),
        "sslcert": str(pki["client.pem"]),
        "sslkey": str(pki["client.key"]),
        "sslpassword": "pw",
    }
    # a certificate file that also holds the key needs no sslkey of its own
    only = postgres_params(SslConfig(cert_file=str(pki["client.pem"])))
    assert only["sslkey"] == only["sslcert"]


def test_mysql_context_follows_the_mode(pki: dict[str, Path]) -> None:
    assert mysql_ssl(SslConfig()) is None
    assert mysql_ssl(SslConfig(mode=SslMode.DISABLE)) is None
    assert mysql_ssl(SslConfig(mode=SslMode.PREFER)) is None
    required = mysql_ssl(SslConfig(mode=SslMode.REQUIRE))
    assert required is not None
    assert required.verify_mode == ssl.CERT_NONE
    assert not required.check_hostname
    ca = SslConfig(mode=SslMode.VERIFY_CA, ca_file=str(pki["ca.pem"]))
    context = mysql_ssl(ca)
    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert not context.check_hostname
    full = mysql_ssl(ca.model_copy(update={"mode": SslMode.VERIFY_FULL}))
    assert full is not None
    assert full.check_hostname


def test_mysql_context_loads_the_client_certificate(pki: dict[str, Path]) -> None:
    assert mysql_ssl(files(pki)) is not None
    encrypted = files(pki, key="client-encrypted.key")
    assert mysql_ssl(encrypted, PASSPHRASE) is not None
    with pytest.raises(SslFileError, match="cannot load the client certificate"):
        mysql_ssl(encrypted, "wrong")

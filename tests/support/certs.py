"""A throw-away PKI for TLS tests: a CA, a server certificate, client certificates.

``python -m tests.support.certs DIR`` writes one to ``DIR`` so a local PostgreSQL / MariaDB can be
switched to TLS (see "Testing TLS" in the README).
"""

from __future__ import annotations

import ipaddress
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

PASSPHRASE = "keypass"


@dataclass(frozen=True)
class Authority:
    certificate: x509.Certificate
    key: ec.EllipticCurvePrivateKey


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def make_ca(common_name: str = "EasyDBMS test CA") -> Authority:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return Authority(certificate, key)


def make_leaf(
    ca: Authority,
    common_name: str,
    *,
    hosts: tuple[str, ...] = (),
    valid_days: int = 3650,
    client: bool = False,
    key: ec.EllipticCurvePrivateKey | None = None,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    """A certificate signed by ``ca``; ``valid_days`` may be negative to make it expired."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    start, end = (
        (now - timedelta(days=1), now + timedelta(days=valid_days))
        if valid_days > 0
        else (now - timedelta(days=30), now + timedelta(days=valid_days))
    )
    names: list[x509.GeneralName] = []
    for host in hosts:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(ca.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]
                if client
                else [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=False,
        )
    )
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    return builder.sign(ca.key, hashes.SHA256()), key


def certificate_pem(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.PEM)


def key_pem(key: ec.EllipticCurvePrivateKey, passphrase: str | None = None) -> bytes:
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )


def write_pki(directory: Path, client_name: str = "erd_cert") -> dict[str, Path]:
    """Write ``ca.pem``, ``server.pem/key``, ``client.pem/key``, ``client-encrypted.key``,
    ``other-ca.pem`` and return their paths by name."""
    directory.mkdir(parents=True, exist_ok=True)
    ca = make_ca()
    other = make_ca("Somebody else's CA")
    server, server_key = make_leaf(ca, "localhost", hosts=("localhost", "127.0.0.1", "::1"))
    client, client_key = make_leaf(ca, client_name, client=True)
    files = {
        "ca.pem": certificate_pem(ca.certificate),
        "other-ca.pem": certificate_pem(other.certificate),
        "server.pem": certificate_pem(server),
        "server.key": key_pem(server_key),
        "client.pem": certificate_pem(client),
        "client.key": key_pem(client_key),
        "client-encrypted.key": key_pem(client_key, PASSPHRASE),
    }
    paths: dict[str, Path] = {}
    for name, data in files.items():
        path = directory / name
        path.write_bytes(data)
        path.chmod(0o600 if name.endswith(".key") else 0o644)
        paths[name] = path
    return paths


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "tls-test-certs")
    for written in write_pki(target).values():
        print(written)

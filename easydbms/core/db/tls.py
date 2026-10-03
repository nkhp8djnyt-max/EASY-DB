"""TLS settings -> what each driver wants, and a look at the certificate files beforehand.

Looking at the files first turns the usual mistakes (a path that does not exist, a key that
belongs to another certificate, a certificate that has expired, an encrypted key without its
passphrase) into one clear sentence instead of an obscure handshake error.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from ..connections import SslConfig, SslMode
from .errors import SslFileError


@dataclass(frozen=True, slots=True)
class FileCheck:
    """What was found in one TLS file."""

    label: str  # "CA certificate", "client certificate", "client key"
    path: str
    detail: str


def _read(label: str, path: str) -> bytes:
    file = Path(path).expanduser()
    if not file.is_file():
        raise SslFileError(f"the {label} file '{path}' does not exist")
    try:
        return file.read_bytes()
    except OSError as error:
        raise SslFileError(f"cannot read the {label} file '{path}': {error.strerror}") from None


def _certificates(label: str, path: str) -> list[x509.Certificate]:
    data = _read(label, path)
    try:
        found = x509.load_pem_x509_certificates(data)
    except ValueError:
        found = []
    if not found:
        raise SslFileError(f"the {label} file '{path}' holds no PEM certificate")
    return found


def _describe(certificate: x509.Certificate, *, enforce: bool = True) -> str:
    names = certificate.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    common = names[0].value if names else certificate.subject.rfc4514_string()
    subject = common.decode() if isinstance(common, bytes) else common
    expires = certificate.not_valid_after_utc
    text = f"{subject}, valid until {expires:%Y-%m-%d}"
    if not enforce:  # a CA bundle may hold old roots that nothing is signed by any more
        return text
    if expires < datetime.now(UTC):
        raise SslFileError(f"the certificate '{subject}' expired on {expires:%Y-%m-%d}")
    if certificate.not_valid_before_utc > datetime.now(UTC):
        starts = certificate.not_valid_before_utc
        raise SslFileError(f"the certificate '{subject}' is not valid before {starts:%Y-%m-%d}")
    return text


def _private_key(path: str, password: str | None) -> object:
    data = _read("client key", path)
    try:
        return serialization.load_pem_private_key(data, password.encode() if password else None)
    except TypeError:
        raise SslFileError(
            f"the client key '{path}' is encrypted: a passphrase is needed"
        ) from None
    except ValueError as error:
        if "password" in str(error).lower() or "decrypt" in str(error).lower():
            raise SslFileError(f"wrong passphrase for the client key '{path}'") from None
        raise SslFileError(
            f"the client key file '{path}' holds no usable PEM private key"
        ) from None


def key_is_encrypted(path: str) -> bool:
    """Does the PEM key at ``path`` need a passphrase? (``False`` if it cannot be read)"""
    try:
        data = Path(path).expanduser().read_bytes()
    except OSError:
        return False
    if b"ENCRYPTED" in data:
        return True
    try:
        serialization.load_pem_private_key(data, None)
    except TypeError:
        return True
    except ValueError:
        return False
    return False


def check_files(ssl_config: SslConfig, key_password: str | None = None) -> list[FileCheck]:
    """Inspect the configured files; raises :class:`SslFileError` for the first problem."""
    checks: list[FileCheck] = []
    if ssl_config.ca_file:
        certificates = _certificates("CA certificate", ssl_config.ca_file)
        details = [_describe(c, enforce=len(certificates) == 1) for c in certificates[:3]]
        more = f" (+{len(certificates) - 3} more)" if len(certificates) > 3 else ""
        checks.append(FileCheck("CA certificate", ssl_config.ca_file, "; ".join(details) + more))
    if ssl_config.cert_file:
        certificates = _certificates("client certificate", ssl_config.cert_file)
        detail = _describe(certificates[0])
        checks.append(FileCheck("client certificate", ssl_config.cert_file, detail))
        key_path = ssl_config.key_file or ssl_config.cert_file
        key = _private_key(key_path, key_password)
        public = getattr(key, "public_key", lambda: None)()
        if public is not None and _public_bytes(public) != _public_bytes(
            certificates[0].public_key()
        ):
            raise SslFileError(
                f"the client key '{key_path}' does not belong to the certificate "
                f"'{ssl_config.cert_file}'"
            )
        checks.append(FileCheck("client key", key_path, "matches the certificate"))
    elif ssl_config.key_file:
        raise SslFileError("a client key needs its client certificate")
    return checks


def file_steps(
    ssl_config: SslConfig, key_password: str | None = None
) -> list[tuple[str, Callable[[], str]]]:
    """The "TLS files" step of a connection test (none when no files are configured)."""
    if not (ssl_config.ca_file or ssl_config.cert_file or ssl_config.key_file):
        return []
    if ssl_config.mode is SslMode.DISABLE:
        return []

    def run() -> str:
        return "; ".join(f"{c.label}: {c.detail}" for c in check_files(ssl_config, key_password))

    return [("TLS files", run)]


def _public_bytes(key: object) -> bytes:
    return key.public_bytes(  # type: ignore[attr-defined,no-any-return]
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


# ---------------------------------------------------------------------- PostgreSQL (libpq)


def postgres_params(ssl_config: SslConfig, key_password: str | None = None) -> dict[str, str]:
    """libpq connection parameters for ``ssl_config``."""
    params: dict[str, str] = {}
    if ssl_config.mode is not None:
        params["sslmode"] = ssl_config.mode.value
    if ssl_config.ca_file:
        params["sslrootcert"] = str(Path(ssl_config.ca_file).expanduser())
    if ssl_config.cert_file:
        params["sslcert"] = str(Path(ssl_config.cert_file).expanduser())
        key = ssl_config.key_file or ssl_config.cert_file
        params["sslkey"] = str(Path(key).expanduser())
        if key_password:
            params["sslpassword"] = key_password
    return params


# ---------------------------------------------------------------------- MySQL / MariaDB


def mysql_ssl(ssl_config: SslConfig, key_password: str | None = None) -> ssl.SSLContext | None:
    """The TLS context for PyMySQL (``None``: no TLS settings, let the driver decide)."""
    mode = ssl_config.mode
    if mode in (None, SslMode.DISABLE, SslMode.ALLOW, SslMode.PREFER) and not (
        ssl_config.cert_file or ssl_config.ca_file
    ):
        return None
    ca = str(Path(ssl_config.ca_file).expanduser()) if ssl_config.ca_file else None
    context = ssl.create_default_context(cafile=ca)
    # Python 3.13 turned on strict X.509 checks that the self-signed certificates MySQL / MariaDB
    # generate for themselves do not pass.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    verifies = mode is not None and mode.verifies
    context.check_hostname = mode is SslMode.VERIFY_FULL
    context.verify_mode = ssl.CERT_REQUIRED if verifies else ssl.CERT_NONE
    if ssl_config.cert_file:
        key = ssl_config.key_file or ssl_config.cert_file
        try:
            context.load_cert_chain(
                str(Path(ssl_config.cert_file).expanduser()),
                str(Path(key).expanduser()),
                key_password,
            )
        except (ssl.SSLError, OSError) as error:
            raise SslFileError(f"cannot load the client certificate: {error}") from None
    return context

"""SSH private keys: loading, and telling whether a passphrase is needed."""

from __future__ import annotations

import base64
import struct
from pathlib import Path
from typing import Any

import paramiko

from .errors import SshKeyError, SshSecretRequired

_OPENSSH_BEGIN = "-----BEGIN OPENSSH PRIVATE KEY-----"
_OPENSSH_END = "-----END OPENSSH PRIVATE KEY-----"
_MAGIC = b"openssh-key-v1\x00"


def is_encrypted(path: str) -> bool:
    """Does the key file need a passphrase? (``False`` when it cannot be read or is not a key)"""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if _OPENSSH_BEGIN in text:
        body = text.split(_OPENSSH_BEGIN, 1)[1].split(_OPENSSH_END, 1)[0]
        try:
            data = base64.b64decode("".join(body.split()))
        except ValueError:
            return False
        if not data.startswith(_MAGIC):
            return False
        (length,) = struct.unpack(">I", data[len(_MAGIC) : len(_MAGIC) + 4])
        cipher = data[len(_MAGIC) + 4 : len(_MAGIC) + 4 + length]
        return cipher != b"none"
    return "ENCRYPTED" in text  # PEM: "Proc-Type: 4,ENCRYPTED" / "BEGIN ENCRYPTED PRIVATE KEY"


def load_key(path: str, passphrase: str | None, field: str = "ssh.passphrase") -> Any:
    """Load a private key file (RSA, ECDSA, Ed25519); ``field`` names the secret to ask for."""
    file = Path(path).expanduser()
    if not file.is_file():
        raise SshKeyError(f"the private key file '{path}' does not exist")
    try:
        return paramiko.PKey.from_path(str(file), passphrase.encode() if passphrase else None)
    except paramiko.PasswordRequiredException:
        raise SshSecretRequired(
            f"the private key '{path}' is protected by a passphrase", field
        ) from None
    except OSError as error:
        raise SshKeyError(f"cannot read the private key '{path}': {error.strerror}") from None
    except paramiko.SSHException as error:
        text = str(error).lower()
        if passphrase and ("passphrase" in text or "password" in text or "decrypt" in text):
            raise SshKeyError(f"wrong passphrase for the private key '{path}'") from None
        if "encrypted" in text and not passphrase:
            raise SshSecretRequired(
                f"the private key '{path}' is protected by a passphrase", field
            ) from None
        raise SshKeyError(f"'{path}' is not a usable private key: {error}") from None
    except (ValueError, TypeError) as error:
        text = str(error).lower()
        if "password" in text or "decrypt" in text:
            if passphrase:
                raise SshKeyError(f"wrong passphrase for the private key '{path}'") from None
            raise SshSecretRequired(
                f"the private key '{path}' is protected by a passphrase", field
            ) from None
        raise SshKeyError(f"'{path}' is not a usable private key: {error}") from None

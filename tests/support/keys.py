"""Key and certificate files for tests."""

from __future__ import annotations

from pathlib import Path

import paramiko


def write_key(
    directory: Path, name: str, passphrase: str | None = None
) -> tuple[paramiko.PKey, str]:
    """A fresh ECDSA key written to ``directory/name`` (optionally encrypted): ``(key, path)``."""
    key = paramiko.ECDSAKey.generate(bits=256)
    path = directory / name
    key.write_private_key_file(str(path), password=passphrase)
    path.chmod(0o600)
    return key, str(path)

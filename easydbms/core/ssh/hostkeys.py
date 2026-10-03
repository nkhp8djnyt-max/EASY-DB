"""Which SSH servers we trust: an OpenSSH-format ``known_hosts`` of our own, plus the user's.

A server whose key is not known is *never* accepted silently: the caller gets
:class:`SshHostKeyUnknown` with the fingerprint, asks the person, and only then calls
:meth:`KnownHosts.trust`. A server whose key *changed* is refused outright.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

import paramiko


class Verdict(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    CHANGED = "changed"


def fingerprint(key: Any) -> str:
    """The OpenSSH way of showing a key: ``SHA256:`` and unpadded base64."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def host_label(host: str, port: int) -> str:
    """How known_hosts names a server: ``host``, or ``[host]:port`` for a non-default port."""
    return host if port == 22 else f"[{host}]:{port}"


class KnownHosts:
    """Our own file (read and written) and any number of read-only ones (``~/.ssh/known_hosts``)."""

    def __init__(self, own: Path, extra: list[Path] | None = None) -> None:
        self._own = own
        self._extra = extra if extra is not None else [Path.home() / ".ssh" / "known_hosts"]

    @staticmethod
    def _load(path: Path) -> Any:
        keys = paramiko.HostKeys()
        with contextlib.suppress(OSError, paramiko.SSHException):
            keys.load(str(path))
        return keys

    def verdict(self, host: str, port: int, key: Any) -> tuple[Verdict, str]:
        """``(verdict, fingerprint of the remembered key of the same type or "")``."""
        name = host_label(host, port)
        remembered: str = ""
        for path in [self._own, *self._extra]:
            entries = self._load(path).lookup(name)
            if entries is None:
                continue
            same = entries.get(key.get_name())
            if same is None:
                continue
            if same.asbytes() == key.asbytes():
                return Verdict.KNOWN, fingerprint(key)
            remembered = fingerprint(same)
        return (Verdict.CHANGED, remembered) if remembered else (Verdict.UNKNOWN, "")

    def trust(self, host: str, port: int, key: Any) -> None:
        """Remember ``key`` for ``host:port`` in our own file."""
        keys = self._load(self._own)
        keys.add(host_label(host, port), key.get_name(), key)
        self._own.parent.mkdir(parents=True, exist_ok=True)
        keys.save(str(self._own))
        with contextlib.suppress(OSError):
            os.chmod(self._own, 0o600)

    def forget(self, host: str, port: int) -> bool:
        """Remove every key remembered for ``host:port`` from our own file."""
        keys = self._load(self._own)
        name = host_label(host, port)
        if keys.lookup(name) is None:
            return False
        del keys[name]
        keys.save(str(self._own))
        return True

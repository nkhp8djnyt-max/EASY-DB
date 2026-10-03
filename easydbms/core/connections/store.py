"""Saved connections, kept in ``connections.json`` (no secrets inside)."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from .models import FileConnection, ServerConnection, parse_config, replace

_FORMAT_VERSION = 1


class ConnectionNotFoundError(KeyError):
    """No saved connection has that id."""


class ConnectionStore:
    """CRUD over the saved connections with atomic file writes.

    A file that cannot be read, or that holds entries that fail validation, is never overwritten:
    the original is moved to ``connections.json.broken-<timestamp>`` first, the valid entries
    are kept and :attr:`recovered_from` tells the UI so it can warn the user.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._items: dict[str, ServerConnection | FileConnection] = {}
        self.recovered_from: Path | None = None
        self.load()

    def load(self) -> None:
        with self._lock:
            self._items = {}
            self.recovered_from = None
            if not self._path.exists():
                return
            try:
                document = json.loads(self._path.read_text(encoding="utf-8"))
                entries = document["connections"]
                if not isinstance(entries, list):
                    raise TypeError("'connections' is not a list")
            except (OSError, ValueError, KeyError, TypeError):
                self._set_aside()
                return
            damaged = False
            for entry in entries:
                try:
                    config = parse_config(entry)
                except (ValidationError, TypeError, ValueError):
                    damaged = True
                    continue
                self._items[config.id] = config
            if damaged:
                self._set_aside()
                self._write()

    def all(self) -> list[ServerConnection | FileConnection]:
        with self._lock:
            return list(self._items.values())

    def get(self, connection_id: str) -> ServerConnection | FileConnection:
        with self._lock:
            try:
                return self._items[connection_id]
            except KeyError:
                raise ConnectionNotFoundError(connection_id) from None

    def find(self, connection_id: str) -> ServerConnection | FileConnection | None:
        with self._lock:
            return self._items.get(connection_id)

    def save(self, config: ServerConnection | FileConnection) -> None:
        """Add the connection, or replace the one with the same id."""
        with self._lock:
            self._items[config.id] = config
            self._write()

    def remove(self, connection_id: str) -> None:
        with self._lock:
            if self._items.pop(connection_id, None) is None:
                raise ConnectionNotFoundError(connection_id)
            self._write()

    def duplicate(self, connection_id: str) -> ServerConnection | FileConnection:
        """A copy with a new id and a "(copy)" suffix; the password is not copied."""
        with self._lock:
            original = self.get(connection_id)
            copy = replace(original, name=f"{original.name} (copy)", id=_new_id())
            self._items[copy.id] = copy
            self._write()
            return copy

    def groups(self) -> list[str]:
        with self._lock:
            return sorted({c.group for c in self._items.values() if c.group})

    # ------------------------------------------------------------------ internals

    def _write(self) -> None:
        document = {
            "version": _FORMAT_VERSION,
            "connections": [c.model_dump(mode="json") for c in self._items.values()],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self._path)

    def _set_aside(self) -> None:
        target = self._path.with_name(f"{self._path.name}.broken-{int(time.time())}")
        try:
            os.replace(self._path, target)
        except OSError:
            return
        self.recovered_from = target


def _new_id() -> str:
    return uuid.uuid4().hex

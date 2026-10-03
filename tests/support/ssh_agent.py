"""A minimal ssh-agent on a unix socket: lists keys and signs, which is all a client needs."""

from __future__ import annotations

import contextlib
import os
import socket
import struct
import threading
from pathlib import Path

import paramiko

_REQUEST_IDENTITIES, _IDENTITIES_ANSWER = 11, 12
_SIGN_REQUEST, _SIGN_RESPONSE = 13, 14
_FAILURE = 5


def _string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


class FakeAgent:
    def __init__(self, directory: Path, keys: list[paramiko.PKey]) -> None:
        self.keys = keys
        self.path = str(directory / "agent.sock")
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(self.path)
        self._socket.listen(5)
        self.signed = 0
        threading.Thread(target=self._loop, daemon=True, name="fake-agent").start()

    def stop(self) -> None:
        with contextlib.suppress(OSError):
            self._socket.close()
        with contextlib.suppress(OSError):
            os.unlink(self.path)

    def _loop(self) -> None:
        while True:
            try:
                client, _ = self._socket.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        with client:
            while True:
                header = self._read(client, 4)
                if header is None:
                    return
                body = self._read(client, struct.unpack(">I", header)[0])
                if body is None:
                    return
                self._send(client, self._answer(body))

    @staticmethod
    def _read(client: socket.socket, size: int) -> bytes | None:
        data = b""
        while len(data) < size:
            try:
                chunk = client.recv(size - len(data))
            except OSError:
                return None
            if not chunk:
                return None
            data += chunk
        return data

    @staticmethod
    def _send(client: socket.socket, payload: bytes) -> None:
        with contextlib.suppress(OSError):
            client.sendall(struct.pack(">I", len(payload)) + payload)

    def _answer(self, body: bytes) -> bytes:
        kind = body[0]
        if kind == _REQUEST_IDENTITIES:
            out = struct.pack(">BI", _IDENTITIES_ANSWER, len(self.keys))
            for key in self.keys:
                out += _string(key.asbytes()) + _string(b"fake agent key")
            return out
        if kind == _SIGN_REQUEST:
            (length,) = struct.unpack(">I", body[1:5])
            blob = body[5 : 5 + length]
            offset = 5 + length
            (data_length,) = struct.unpack(">I", body[offset : offset + 4])
            data = body[offset + 4 : offset + 4 + data_length]
            (flags,) = struct.unpack(
                ">I", body[offset + 4 + data_length : offset + 8 + data_length]
            )
            for key in self.keys:
                if key.asbytes() == blob:
                    algorithm = {2: "rsa-sha2-256", 4: "rsa-sha2-512"}.get(flags, key.get_name())
                    signature = key.sign_ssh_data(data, algorithm)
                    self.signed += 1
                    return bytes([_SIGN_RESPONSE]) + _string(signature.asbytes())
        return bytes([_FAILURE])

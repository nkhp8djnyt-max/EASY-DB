from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from easydbms.core.connections import SshAuth, SshConfig, SshHop
from easydbms.core.ssh import KnownHosts
from tests.support.ssh_server import EchoServer, FakeSshServer


@pytest.fixture
def echo() -> Iterator[EchoServer]:
    with EchoServer() as server:
        yield server


@pytest.fixture
def known(tmp_path: Path) -> KnownHosts:
    """Our known-hosts file only (the developer's ~/.ssh/known_hosts must not matter)."""
    return KnownHosts(tmp_path / "known_hosts", extra=[])


@pytest.fixture
def server() -> Iterator[FakeSshServer]:
    with FakeSshServer(users={"alice": "wonderland"}) as running:
        yield running


def hop(server: FakeSshServer, **changes: object) -> SshHop:
    data: dict[str, object] = {
        "host": "127.0.0.1",
        "port": server.port,
        "user": "alice",
        "auth": SshAuth.PASSWORD,
    }
    return SshHop.model_validate({**data, **changes})


def config(server: FakeSshServer, jump: SshHop | None = None, **changes: object) -> SshConfig:
    return SshConfig(server=hop(server, **changes), jump=jump)

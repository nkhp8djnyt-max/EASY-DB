"""Network checks used by ``DatabaseClient.test()`` before logging in."""

from __future__ import annotations

import socket
from collections.abc import Callable

from .errors import ConnectTimeout, DnsError, PortClosed

DEFAULT_TIMEOUT = 10.0


def network_steps(
    host: str, port: int | None, timeout: float = DEFAULT_TIMEOUT
) -> list[tuple[str, Callable[[], str]]]:
    """DNS then TCP checks for ``host:port``; none for unix sockets or when no port is known."""
    if host.startswith("/") or port is None:
        return []
    resolved: list[tuple[socket.AddressFamily, str]] = []

    def dns() -> str:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise DnsError(f"cannot resolve host '{host}': {error.strerror or error}") from None
        resolved.extend((info[0], str(info[4][0])) for info in infos)
        return f"{host} -> {resolved[0][1]}"

    def tcp() -> str:
        last: OSError | None = None
        for family, address in resolved:
            try:
                with socket.socket(family, socket.SOCK_STREAM) as probe:
                    probe.settimeout(timeout)
                    probe.connect((address, port))
                return f"{address}:{port}"
            except TimeoutError:
                raise ConnectTimeout(
                    f"{address}:{port} did not answer within {timeout:g}s "
                    "(is a firewall dropping packets?)"
                ) from None
            except OSError as error:
                last = error
        raise PortClosed(
            f"{host}:{port} refused the connection"
            + (f": {last.strerror}" if last and last.strerror else "")
        )

    return [("DNS", dns), ("TCP", tcp)]

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from easydbms.core.connections import (
    ServiceNotFoundError,
    find_service,
    load_pgpass,
    load_services,
    parse_pgpass,
    parse_service_file,
    pgpass_path,
    service_file_paths,
)

PGPASS = r"""
# comment
db.example.com:5432:sales:alice:secret1
*:*:*:bob:with\:colon
localhost:*:*:*:wild\\card

   # indented comment
not-enough-fields:1:2
db.example.com:5432:sales:alice:shadowed
"""


def test_pgpass_lines_are_parsed_with_their_numbers() -> None:
    parsed = parse_pgpass(PGPASS)
    assert [(e.host, e.user, e.line) for e in parsed.entries] == [
        ("db.example.com", "alice", 3),
        ("*", "bob", 4),
        ("localhost", "*", 5),
        ("db.example.com", "alice", 9),
    ]


def test_escaped_colons_and_backslashes_are_literal() -> None:
    parsed = parse_pgpass(PGPASS)
    assert parsed.entries[1].password == "with:colon"
    assert parsed.entries[2].password == "wild\\card"


def test_the_first_matching_line_wins() -> None:
    found = parse_pgpass(PGPASS).lookup("db.example.com", 5432, "sales", "alice")
    assert found is not None
    assert (found.password, found.line) == ("secret1", 3)


def test_wildcards_match_anything() -> None:
    parsed = parse_pgpass(PGPASS)
    found = parsed.lookup("elsewhere", 6543, "any", "bob")
    assert found is not None
    assert found.password == "with:colon"
    assert parsed.lookup("db.example.com", 5432, "sales", "mallory") is None
    assert parsed.lookup("db.example.com", 5433, "sales", "alice") is None


def test_the_default_port_applies_when_none_is_given() -> None:
    found = parse_pgpass("h:5432:d:u:pw").lookup("h", None, "d", "u")
    assert found is not None
    assert found.password == "pw"


def test_a_socket_directory_or_empty_host_means_localhost() -> None:
    parsed = parse_pgpass("localhost:*:*:u:pw")
    assert parsed.lookup("/var/run/postgresql", 5432, "d", "u") is not None
    assert parsed.lookup("", 5432, "d", "u") is not None


def test_crlf_line_endings_are_handled() -> None:
    found = parse_pgpass("h:1:d:u:pw\r\nx:2:d:u:other\r\n").lookup("x", 2, "d", "u")
    assert found is not None
    assert found.password == "other"


def test_pgpassfile_overrides_the_default_location(tmp_path: Path) -> None:
    assert pgpass_path({"PGPASSFILE": str(tmp_path / "mine")}) == tmp_path / "mine"
    assert pgpass_path({"HOME": str(tmp_path)}) == tmp_path / ".pgpass"
    assert pgpass_path({"APPDATA": str(tmp_path)}, windows=True) == (
        tmp_path / "postgresql" / "pgpass.conf"
    )


def test_a_missing_file_is_simply_absent(tmp_path: Path) -> None:
    assert load_pgpass({"PGPASSFILE": str(tmp_path / "nope")}) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_file_other_users_can_read_is_ignored(tmp_path: Path) -> None:
    file = tmp_path / "pgpass"
    file.write_text("h:1:d:u:pw\n")
    file.chmod(0o644)
    loaded = load_pgpass({"PGPASSFILE": str(file)})
    assert loaded is not None
    assert "chmod 0600" in loaded.problem
    assert loaded.lookup("h", 1, "d", "u") is None
    file.chmod(0o600)
    usable = load_pgpass({"PGPASSFILE": str(file)})
    assert usable is not None
    assert not usable.problem
    found = usable.lookup("h", 1, "d", "u")
    assert found is not None
    assert found.password == "pw"


SERVICES = """
# user file
[prod]
host=db.prod.example.com
port=6432
dbname=sales
user=app
password=svc-secret
sslmode=verify-full
sslrootcert = /etc/ca.pem

[empty]

[dev]
host=localhost
"""


def test_services_are_parsed_by_section() -> None:
    parsed = parse_service_file(SERVICES, Path("/x/pg_service.conf"))
    assert set(parsed) == {"prod", "empty", "dev"}
    assert dict(parsed["prod"].params)["sslrootcert"] == "/etc/ca.pem"
    assert parsed["prod"].path == Path("/x/pg_service.conf")
    assert dict(parsed["empty"].params) == {}


def test_service_files_come_from_the_environment(tmp_path: Path) -> None:
    mine = tmp_path / "mine.conf"
    system = tmp_path / "sys"
    paths = service_file_paths({"PGSERVICEFILE": str(mine), "PGSYSCONFDIR": str(system)})
    assert paths == [mine, system / "pg_service.conf"]
    assert service_file_paths({"HOME": str(tmp_path)})[0] == tmp_path / ".pg_service.conf"


def test_the_user_file_wins_over_the_system_file(tmp_path: Path) -> None:
    mine = tmp_path / "mine.conf"
    mine.write_text("[a]\nhost=user\n")
    system = tmp_path / "sys"
    system.mkdir()
    (system / "pg_service.conf").write_text("[a]\nhost=system\n[b]\nhost=only-system\n")
    env = {"PGSERVICEFILE": str(mine), "PGSYSCONFDIR": str(system)}
    services = load_services(env)
    assert dict(services["a"].params)["host"] == "user"
    assert dict(services["b"].params)["host"] == "only-system"
    assert find_service("b", env).path == system / "pg_service.conf"


def test_an_unknown_service_names_where_it_looked(tmp_path: Path) -> None:
    env = {"PGSERVICEFILE": str(tmp_path / "none.conf"), "PGSYSCONFDIR": str(tmp_path)}
    with pytest.raises(ServiceNotFoundError, match=r"'ghost'.*none\.conf"):
        find_service("ghost", env)
    assert isinstance(ServiceNotFoundError("x", []), ValueError)


def test_pgpass_helpers_work_without_the_home_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGPASSFILE", raising=False)
    assert pgpass_path().name in (".pgpass", "pgpass.conf")
    assert os.environ.get("PGPASSFILE") is None

from __future__ import annotations

import pytest

from easydbms.core.db.clients.postgres import _clean_connect_message
from easydbms.core.db.clients.sqlite import _human_size


def test_libpq_reports_both_ssl_attempts_but_the_user_sees_one() -> None:
    raw = (
        'connection failed: connection to server at "127.0.0.1", port 5432 failed: FATAL:  '
        'password authentication failed for user "erd"\n'
        'connection to server at "127.0.0.1", port 5432 failed: FATAL:  '
        'password authentication failed for user "erd"'
    )
    cleaned = _clean_connect_message(raw)
    assert cleaned.count("password authentication failed") == 1
    assert not cleaned.startswith("connection failed")


def test_different_lines_are_all_kept_in_order() -> None:
    assert _clean_connect_message("a\n\n  b  \na\nc") == "a\nb\nc"
    assert _clean_connect_message("") == ""


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (1, "1 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (5000 * 1024**3, "5000.0 GB"),
    ],
)
def test_human_size(size: int, text: str) -> None:
    assert _human_size(size) == text

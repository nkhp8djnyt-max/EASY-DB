"""The native scanner must agree with the Python reference on every input it accepts."""

from __future__ import annotations

import os
import random
import subprocess
import sys
from collections.abc import Iterator

import pytest

from sql_erd_studio.core import simd
from sql_erd_studio.core.dialects import Dialect, all_dialects
from sql_erd_studio.core.dialects import splitter as splitter_module

pytestmark = pytest.mark.skipif(not simd.available(), reason="native extension not built")

ITERATIONS = int(os.environ.get("ERD_FUZZ_ITERATIONS", "3000"))

# Fragments that exercise every lexer branch; random glue between them makes odd boundaries.
FRAGMENTS = [
    "select",
    "SELECT",
    "from",
    "t",
    "x",
    "1",
    "1.5",
    "1e5",
    "1e+",
    ".5",
    "0x1F",
    "1_0",
    "a$b",
    ";",
    ";;",
    ",",
    "(",
    ")",
    ".",
    "[",
    "]",
    "*",
    "=",
    "<>",
    "::",
    "||",
    "+",
    "-",
    "--",
    "-- ",
    "--x",
    "-- note;\n",
    "#",
    "# c;\n",
    "/*",
    "*/",
    "/* c; */",
    "/* /* n */ */",
    "'",
    "''",
    "'a;b'",
    "'it''s'",
    "'\\'",
    "\\",
    '"',
    '"a;b"',
    "`",
    "`a;b`",
    "$$",
    "$tag$",
    "$a$ ; $a$",
    "$1",
    "$x",
    "?",
    "?12",
    ":p",
    ":",
    "@v",
    "@@g",
    "@",
    "E'a\\'b;'",
    "e'",
    "\n",
    " ",
    "\t",
    "\r\n",
    "  ",
    "CREATE",
    "create",
    "TRIGGER",
    "PROCEDURE",
    "FUNCTION",
    "EVENT",
    "OR",
    "REPLACE",
    "TEMP",
    "BEGIN",
    "begin",
    "END",
    "end",
    "CASE",
    "IF",
    "LOOP",
    "WHILE",
    "REPEAT",
    "THEN",
    "ELSE",
    "BEGIN ATOMIC",
    "END IF",
    "END CASE",
    "END LOOP",
    "AFTER INSERT ON t FOR EACH ROW",
    # non-ASCII: letters, decimal digits of other scripts, superscripts, case-folding oddities
    "привет",
    "'строка;'",
    "-- комментарий;\n",
    "日本語",
    "€",
    "é",
    "٣",
    "1٣",
    ".٣",
    "$٣",
    "1e٣",
    "²",
    "ß",
    "ı",
    "ſ",
    "ﬁ",
    "BEGıN",
    "ENſ",
    "ENDı",
    "BEGIN ",
    " ",
    "\x1c",
    "\x00",
    "😀",
    "'😀;'",
]
ASCII_FRAGMENTS = [f for f in FRAGMENTS if f.isascii()]
GLUE = ["", "", " ", "\n", " ", "\t"]


def random_script(rng: random.Random, fragments: list[str]) -> str:
    parts: list[str] = []
    for _ in range(rng.randint(1, 60)):
        parts.append(rng.choice(fragments))
        parts.append(rng.choice(GLUE))
    return "".join(parts)


def random_noise(rng: random.Random, alphabet: str) -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))


@pytest.fixture(params=[True, False], ids=["vector", "scalar"])
def vector(request: pytest.FixtureRequest) -> Iterator[bool]:
    simd.set_vector_enabled(request.param)
    yield request.param
    simd.set_vector_enabled(True)


# The only characters allowed to make the native splitter decline: non-ASCII decimal digits and
# characters whose str.upper() contains ASCII letters.
DECLINE_TRIGGERS = frozenset(
    chr(c)
    for c in range(128, 0x110000)
    if chr(c).isdecimal() or any(ord(ch) < 128 for ch in chr(c).upper())
)


def assert_same(sql: str, dialect: Dialect) -> bool:
    """Compare both splitters on ``sql``; ``False`` if the native one declined."""
    expected = splitter_module._split_python(sql, dialect)
    actual = splitter_module._split_native(sql, dialect)
    if actual is None:
        assert any(ch in DECLINE_TRIGGERS for ch in sql), f"unjustified decline: {sql!r}"
        return False
    assert actual == expected, f"{dialect.id.value}: {sql!r}"
    return True


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_fuzz_fragments(dialect: Dialect, vector: bool) -> None:
    rng = random.Random(f"fragments-{dialect.id.value}")
    accepted = sum(assert_same(random_script(rng, FRAGMENTS), dialect) for _ in range(ITERATIONS))
    assert accepted > ITERATIONS * 0.5  # most scripts, exotic characters included, run natively


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_ascii_input_is_never_declined(dialect: Dialect, vector: bool) -> None:
    rng = random.Random(f"ascii-{dialect.id.value}")
    for _ in range(ITERATIONS):
        assert assert_same(random_script(rng, ASCII_FRAGMENTS), dialect)


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_fuzz_raw_characters(dialect: Dialect, vector: bool) -> None:
    alphabet = "ab 1;'\"`$-/*#\\\n(){}[]@?:.,eEBGINDCASTRLж١ßı"
    rng = random.Random(f"noise-{dialect.id.value}")
    for _ in range(ITERATIONS):
        assert_same(random_noise(rng, alphabet), dialect)


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_every_truncation_of_a_tricky_script(dialect: Dialect, vector: bool) -> None:
    """Unterminated strings, comments and $$ bodies at every possible cut position."""
    script = (
        "-- c;\nselect 'a;''b', \"q;\", `r;`, $$ x; $$, $t$ y; $t$, E'\\';' /* ; /* n; */ */ ;"
        "\nCREATE TRIGGER t AFTER INSERT ON a BEGIN SELECT CASE WHEN 1 THEN 2 END; END IF; END;"
        "\nselect 1e5, 0x1F, .5, $1, :p, @v, ?1; # tail;\n-- end"
    )
    for cut in range(len(script) + 1):
        assert_same(script[:cut], dialect)


@pytest.mark.parametrize("width", [1, 2, 4])
def test_large_scripts_across_vector_boundaries(width: int, vector: bool) -> None:
    """Statements longer than the 16/32-byte vector, with terminators at every offset."""
    filler = {1: "a", 2: "ж", 4: "😀"}[width]
    for dialect in all_dialects():
        for pad in range(0, 70):
            body = filler * pad
            sql = f"select '{body};' /* {body} ; */ , \"{body}\"; -- {body};\nselect 2;"
            assert assert_same(sql, dialect)


def test_find_first_of_matches_str_find(vector: bool) -> None:
    rng = random.Random("find")
    for _ in range(ITERATIONS):
        width = rng.choice(["a", "ж", "😀"])
        text = "".join(
            rng.choice([width, "x", "'", "*", "\n", ";"]) for _ in range(rng.randint(0, 150))
        )
        needles = "".join(rng.sample("'*\n;$\\\"/", rng.randint(1, 8)))
        start = rng.randint(0, len(text) + 2)
        expected = min((i for i in (text.find(n, start) for n in needles) if i != -1), default=-1)
        assert simd.find_first_of(text, start, needles) == expected, (text, start, needles)


def test_find_first_of_rejects_bad_needles() -> None:
    with pytest.raises(ValueError, match="ASCII"):
        simd.find_first_of("abc", 0, "ж")
    with pytest.raises(ValueError, match="needles"):
        simd.find_first_of("abc", 0, "")
    with pytest.raises(ValueError, match="needles"):
        simd.find_first_of("abc", 0, "123456789")


def test_case_folding_characters_never_cause_a_difference() -> None:
    """``EN<c>`` with a c whose ``str.upper()`` contains ASCII is compared like ``END``."""
    special = [c for c in range(128, 0x110000) if any(ord(ch) < 128 for ch in chr(c).upper())]
    assert special  # the set is non-empty on every Python; the C code lists them explicitly
    for dialect in all_dialects():
        for code in special:
            char = chr(code)
            for word in (f"EN{char}", f"BEG{char}N", f"{char}F", f"CAS{char}", char):
                assert_same(f"CREATE TRIGGER t BEGIN SELECT 1; {word} ; END;", dialect)
                assert_same(f"{char}REATE TRIGGER t BEGIN SELECT 1; END;", dialect)


def test_flags_for_unsupported_spec_declines() -> None:
    from dataclasses import replace

    spec = all_dialects()[0].lexer
    assert simd.flags_for(spec) is not None
    assert simd.flags_for(replace(spec, line_comments=("--", "//"))) is None
    assert simd.flags_for(replace(spec, line_comments=("#",))) is None


def test_disabled_by_environment() -> None:
    code = "from sql_erd_studio.core import simd; print(simd.available(), simd.implementation())"
    env = {**os.environ, "SQL_ERD_STUDIO_NO_SIMD": "1"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.stdout.split() == ["False", "python"], out.stderr


def test_status_reports_implementation() -> None:
    status = simd.status()
    assert status["native"] is True
    assert status["implementation"] in {"avx2", "sse2", "scalar"}
    simd.set_enabled(False)
    try:
        assert simd.implementation() == "python"
    finally:
        simd.set_enabled(True)

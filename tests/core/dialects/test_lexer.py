from __future__ import annotations

import random

import pytest

from sql_erd_studio.core.dialects import (
    MYSQL,
    POSTGRESQL,
    SQLITE,
    Dialect,
    TokenKind,
    all_dialects,
    tokenize,
)

K = TokenKind


def lex(sql: str, dialect: Dialect) -> list[tuple[TokenKind, str]]:
    return [(t.kind, t.text) for t in tokenize(sql, dialect) if t.kind is not K.WHITESPACE]


def test_postgresql_basics() -> None:
    assert lex("select a::int, $1 -- tail\n", POSTGRESQL) == [
        (K.WORD, "select"),
        (K.WORD, "a"),
        (K.OPERATOR, "::"),
        (K.WORD, "int"),
        (K.PUNCT, ","),
        (K.PARAM, "$1"),
        (K.LINE_COMMENT, "-- tail"),
    ]


@pytest.mark.parametrize("literal", ["$$a;b$$", "$f$ x $$ y $f$", "$$$$", "$tag$it's; fine$tag$"])
def test_postgresql_dollar_quoting_is_one_string(literal: str) -> None:
    assert lex(f"select {literal}", POSTGRESQL)[1] == (K.STRING, literal)


def test_postgresql_backslash_only_escapes_in_e_strings() -> None:
    # standard string: the backslash is literal, so the quote after it closes the string
    assert lex(r"'it\'s'", POSTGRESQL)[0] == (K.STRING, r"'it\'")
    assert lex(r"E'it\'s'", POSTGRESQL) == [(K.STRING, r"E'it\'s'")]


def test_postgresql_nested_block_comments_and_jsonb_operators() -> None:
    assert lex("/* a /* b */ c */ x", POSTGRESQL) == [
        (K.BLOCK_COMMENT, "/* a /* b */ c */"),
        (K.WORD, "x"),
    ]
    assert (K.OPERATOR, "?") in lex("a ? 'k'", POSTGRESQL)
    assert (K.OPERATOR, "@>") in lex("a @> b", POSTGRESQL)


def test_mysql_comment_styles() -> None:
    assert lex("# hash\nx", MYSQL) == [(K.LINE_COMMENT, "# hash"), (K.WORD, "x")]
    assert lex("-- dash\nx", MYSQL)[0] == (K.LINE_COMMENT, "-- dash")
    # "--" without following whitespace is two minus signs, not a comment
    assert lex("1--1", MYSQL) == [(K.NUMBER, "1"), (K.OPERATOR, "--"), (K.NUMBER, "1")]
    # but it is a comment in the other dialects
    assert lex("1--1", POSTGRESQL) == [(K.NUMBER, "1"), (K.LINE_COMMENT, "--1")]
    assert lex("/* a /* b */ c */", MYSQL)[0] == (K.BLOCK_COMMENT, "/* a /* b */")


def test_mysql_strings_identifiers_and_variables() -> None:
    assert lex(r"'it\'s'", MYSQL) == [(K.STRING, r"'it\'s'")]
    assert lex("'it''s'", MYSQL) == [(K.STRING, "'it''s'")]
    assert lex('"double"', MYSQL) == [(K.STRING, '"double"')]
    assert lex("`a``b`", MYSQL) == [(K.QUOTED_IDENT, "`a``b`")]
    assert lex("@v @@global.x ?", MYSQL) == [
        (K.VARIABLE, "@v"),
        (K.VARIABLE, "@@global.x"),
        (K.PARAM, "?"),
    ]


def test_sqlite_identifiers_and_params() -> None:
    assert lex('[a b] "x""y" `z`', SQLITE) == [
        (K.QUOTED_IDENT, "[a b]"),
        (K.QUOTED_IDENT, '"x""y"'),
        (K.QUOTED_IDENT, "`z`"),
    ]
    assert lex("?1 :name @name $name ?", SQLITE) == [
        (K.PARAM, "?1"),
        (K.PARAM, ":name"),
        (K.PARAM, "@name"),
        (K.PARAM, "$name"),
        (K.PARAM, "?"),
    ]


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_numbers_words_and_operators(dialect: Dialect) -> None:
    assert [t for t in lex("1 1.5e-3 .5 0xFF", dialect) if t[0] is K.NUMBER] == [
        (K.NUMBER, "1"),
        (K.NUMBER, "1.5e-3"),
        (K.NUMBER, ".5"),
        (K.NUMBER, "0xFF"),
    ]
    assert lex("t1.col", dialect) == [(K.WORD, "t1"), (K.PUNCT, "."), (K.WORD, "col")]
    assert lex("naïve café", dialect) == [(K.WORD, "naïve"), (K.WORD, "café")]
    # an operator run must stop where a comment starts
    assert lex("a+--c\nb", POSTGRESQL)[1:3] == [(K.OPERATOR, "+"), (K.LINE_COMMENT, "--c")]
    assert lex("a>=b", dialect) == [(K.WORD, "a"), (K.OPERATOR, ">="), (K.WORD, "b")]


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("select 'abc", K.STRING),
        ("select /* abc", K.BLOCK_COMMENT),
        ("select 'abc\\", K.STRING),
    ],
)
def test_unterminated_input_is_flagged_not_raised(
    dialect: Dialect, sql: str, kind: TokenKind
) -> None:
    last = tokenize(sql, dialect)[-1]
    assert last.kind is kind
    assert not last.terminated


def test_unterminated_quoted_identifiers() -> None:
    assert not tokenize('select "abc', POSTGRESQL)[-1].terminated
    assert not tokenize("select `abc", MYSQL)[-1].terminated
    assert not tokenize("select [abc", SQLITE)[-1].terminated
    assert not tokenize("select $$abc", POSTGRESQL)[-1].terminated


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_tokens_are_contiguous_and_cover_any_input(dialect: Dialect) -> None:
    rng = random.Random(1234)
    alphabet = [*"ab_1 \n\t'\"`[]()$:;,.-/*#@?\\=<>!|&%e0xE", "--", "/*", "*/", "$$", "::", "é"]
    for _ in range(400):
        sql = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        tokens = tokenize(sql, dialect)
        assert "".join(t.text for t in tokens) == sql
        position = 0
        for token in tokens:
            assert token.start == position
            assert token.end > token.start
            assert sql[token.start : token.end] == token.text
            position = token.end
        assert position == len(sql)

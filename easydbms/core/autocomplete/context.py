"""What the cursor is looking at: clause, visible tables and aliases, what may come next.

The analysis works on the *current statement only* and tolerates anything a user types half-way:
unbalanced parentheses, a missing ``FROM`` (the tables that come *after* the cursor are used), a
word that is still being typed. It is token based on purpose, because a parser rejects exactly the
statements we are asked to complete. ``sqlglot`` is used where it is robust: to read the output
column names of a *finished* subquery or CTE (the token reader is the fallback and also expands
``*``).

Vocabulary: a *group* is a parenthesised part of the statement; a *scope* is the statement itself or
a parenthesised ``SELECT``; a *block* is one ``SELECT`` of a scope (``UNION`` starts a new block); a
*segment* is the part of a block under one clause keyword.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise

import sqlglot
from sqlglot.errors import SqlglotError

from ..dialects import Dialect, Token, TokenKind, split_statements, tokenize
from ..schema import Table
from .index import SchemaIndex


class Expect(StrEnum):
    #: Nothing sensible to offer: inside a string or comment, a number, a name being invented.
    NOTHING = "nothing"
    #: The first word of a statement (or of a subquery).
    STATEMENT = "statement"
    #: A table, view or CTE name.
    TABLE = "table"
    #: Start of an expression: columns, functions, ``CASE``, ``NULL`` ...
    OPERAND = "operand"
    #: After a complete expression or table: operators and the next clause.
    AFTER_OPERAND = "after_operand"
    #: Inside the parentheses of ``INSERT INTO t (...)``, ``ON CONFLICT``, ``USING``, an index.
    COLUMN_LIST = "column_list"
    #: Left-hand side of ``UPDATE t SET <column> =``.
    SET_TARGET = "set_target"
    #: A data type (after ``::``, ``CAST(x AS`` or a column name in ``CREATE TABLE``).
    TYPE = "type"
    #: After ``alias.``, ``table.`` or ``schema.``.
    QUALIFIED = "qualified"
    #: A column of the table being altered (``ALTER TABLE t DROP COLUMN |``).
    TARGET_COLUMN = "target_column"


@dataclass(frozen=True, slots=True)
class SourceColumn:
    name: str
    type: str = ""
    primary_key: bool = False
    foreign_key: bool = False
    nullable: bool = True
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class Source:
    """Something that can stand in a ``FROM`` clause: a table, a CTE or a derived table."""

    alias: str | None
    name: str
    table: Table | None
    columns: tuple[SourceColumn, ...]
    #: ``table``, ``view``, ``cte``, ``derived`` or ``unknown`` (a name the schema does not know).
    kind: str

    @property
    def ref(self) -> str:
        """How columns of this source are qualified in SQL."""
        return self.alias or self.name


@dataclass(slots=True)
class CursorContext:
    expect: Expect
    prefix: str = ""
    #: Range of text an accepted suggestion replaces (offsets into the whole editor text).
    replace_start: int = 0
    replace_end: int = 0
    #: The opening quote when the cursor is inside ``"..."`` / ```...``` / ``[...]``.
    quote: str | None = None
    #: Clause the cursor is in: ``SELECT``, ``FROM``, ``JOIN``, ``JOIN ON``, ``WHERE`` ...
    clause: str = ""
    qualifier: tuple[str, ...] = ()
    sources: list[Source] = field(default_factory=list)
    outer_sources: list[Source] = field(default_factory=list)
    ctes: list[Source] = field(default_factory=list)
    select_aliases: list[str] = field(default_factory=list)
    #: The source a ``JOIN ... ON`` condition is being written for.
    join_target: Source | None = None
    #: The table being inserted into / updated / altered.
    target: Source | None = None
    #: A ``*`` (or ``alias.*``) the cursor sits right after: ``(start, end, alias or None)``.
    star: tuple[int, int, str | None] | None = None
    first_word: str = ""
    #: Upper-case word just before the cursor ("" when it is not a word).
    prev_word: str = ""
    #: The cursor is the first thing inside an unfinished ``(``.
    group_start: bool = False
    #: ``True`` when only a subquery can start here (``FROM (``, ``AS (`` of a CTE).
    subquery_only: bool = False
    in_values: bool = False
    #: Upper-case words of the statement before the cursor (the last few).
    words: tuple[str, ...] = ()
    #: Column names already written in the list being completed (not offered again).
    listed: tuple[str, ...] = ()


# ---------------------------------------------------------------------- word lists

_OPERAND_WORDS = frozenset(
    "SELECT DISTINCT ALL WHERE AND OR NOT ON HAVING BY WHEN THEN ELSE CASE IN BETWEEN LIKE ILIKE "
    "SIMILAR IS EXISTS ANY SOME SET RETURNING ESCAPE DIV MOD XOR REGEXP RLIKE GLOB MATCH "
    "OVER FILTER WITHIN NULLS".split()
)
_NOTHING_WORDS = frozenset({"AS", "LIMIT", "OFFSET", "INTERVAL", "TOP", "FETCH"})
_NOT_ALIAS = frozenset(
    "WHERE JOIN INNER LEFT RIGHT FULL CROSS NATURAL OUTER ON USING GROUP ORDER LIMIT OFFSET HAVING "
    "UNION INTERSECT EXCEPT MINUS SET VALUES RETURNING WINDOW FETCH FOR STRAIGHT_JOIN SELECT FROM "
    "LATERAL WITH".split()
)
_JOIN_MODIFIERS = frozenset({"NATURAL", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER"})
_SET_OPERATORS = frozenset({"UNION", "INTERSECT", "EXCEPT", "MINUS"})
_SIMPLE_CLAUSES = frozenset(
    {"WHERE", "HAVING", "WINDOW", "LIMIT", "OFFSET", "FETCH", "RETURNING", "SET", "VALUES", "FROM",
     "SELECT", "USING"}
)  # fmt: skip
_QUERY_STARTERS = frozenset({"SELECT", "WITH", "VALUES", "TABLE"})
_CLAUSE_STARTS = frozenset(
    "FROM WHERE GROUP ORDER HAVING LIMIT UNION INTERSECT EXCEPT JOIN INNER LEFT RIGHT FULL CROSS "
    "NATURAL RETURNING WINDOW".split()
)
_MAX_DEPTH = 6  # nested CTE / derived-table resolution, guards against self-referencing CTEs
_STATEMENT_PREFIXES = ("EXPLAIN",)
_EXPLAIN_WORDS = frozenset({"ANALYZE", "ANALYSE", "VERBOSE", "QUERY", "PLAN", "EXTENDED", "FORMAT"})


# ---------------------------------------------------------------------- parse structures


@dataclass(slots=True)
class _Group:
    open: int  # index of "(" in the significant token list
    close: int  # index of the matching ")" (len(tokens) when unclosed)
    parent: int  # index of the enclosing group, -1 for the statement itself
    query: bool = False


@dataclass(slots=True)
class _Segment:
    name: str
    kw: int  # absolute index of the first keyword token
    start: int  # position in the level list of the first body token
    stop: int  # position in the level list one past the last body token
    block: int


@dataclass(slots=True)
class _Ref:
    parts: tuple[str, ...]
    alias: str | None
    derived: int  # group index of a derived table, -1 otherwise
    segment: str
    start: int  # absolute index of the first token of the reference


# ---------------------------------------------------------------------- entry point


def analyze(
    sql: str, offset: int, dialect: Dialect, index: SchemaIndex | None = None
) -> CursorContext:
    """Describe the cursor at ``offset`` in ``sql``; ``index`` supplies the schema (optional)."""
    offset = max(0, min(offset, len(sql)))
    begin, end = _statement_bounds(sql, offset, dialect)
    text = sql[begin:end]
    return _Analyzer(text, offset - begin, begin, dialect, index).run()


def _statement_bounds(sql: str, offset: int, dialect: Dialect) -> tuple[int, int]:
    """Range of ``sql`` holding the statement the cursor belongs to (without its ``;``)."""
    statements = split_statements(sql, dialect)
    previous_end = 0
    for statement in statements:
        terminated = statement.body != statement.text
        stop = statement.end - 1 if terminated else statement.end
        if offset < statement.start:
            return previous_end, statement.start  # blank space before this statement
        if offset <= stop:
            return statement.start, stop
        previous_end = statement.end
    return previous_end, len(sql)


# ---------------------------------------------------------------------- the analyzer


class _Analyzer:
    def __init__(
        self, text: str, cur: int, shift: int, dialect: Dialect, index: SchemaIndex | None
    ) -> None:
        self.text = text
        self.cur = cur
        self.shift = shift
        self.dialect = dialect
        self.index = index
        self.sig: list[Token] = []
        self.ups: list[str] = []
        self.groups: list[_Group] = []
        self.group_of: list[int] = []
        self._segments_cache: dict[int, list[_Segment]] = {}
        self._cte_cache: dict[tuple[int, str], Source] = {}
        self._cte_busy: set[tuple[int, str]] = set()
        self._closed_here = False

    # ------------------------------------------------------------------ main

    def run(self) -> CursorContext:
        located = self._locate()
        if located is not None:
            current, _, quote = located
            if quote and current is not None and not current.terminated and current.end > self.cur:
                # ``SELECT "na| FROM t``: the open quote would swallow the rest of the statement,
                # so close the identifier at the cursor and analyse the rest as usual.
                closer = "]" if quote == "[" else quote
                self.text = self.text[: self.cur] + closer + self.text[self.cur :]
                self._closed_here = True
                located = self._locate()
        if located is None:
            return CursorContext(Expect.NOTHING, replace_start=self.shift + self.cur,
                                 replace_end=self.shift + self.cur)  # fmt: skip
        current, prefix, quote = located
        self.sig = [
            t
            for t in tokenize(self.text, self.dialect)
            if not t.is_trivia and (current is None or t.start != current.start)
        ]
        self.ups = [t.upper if t.kind is TokenKind.WORD else "" for t in self.sig]
        ci = sum(1 for t in self.sig if t.end <= (current.start if current else self.cur))
        start = current.start if current else self.cur
        stop = current.end if current else self.cur
        if (
            quote is not None
            and current is not None
            and (not current.terminated or self._closed_here)
        ):
            stop = self.cur
        ctx = CursorContext(
            Expect.NOTHING,
            prefix=prefix,
            replace_start=self.shift + start,
            replace_end=self.shift + stop,
            quote=quote,
        )
        self._build_groups(ci)
        ctx.first_word = self.ups[0] if self.ups else ""
        ctx.words = tuple(w for w in self.ups[:ci] if w)[-8:]
        ctx.prev_word = self.ups[ci - 1] if ci > 0 else ""
        self._describe(ctx, ci)
        return ctx

    def _locate(self) -> tuple[Token | None, str, str | None] | None:
        """The word being typed, its prefix and quote; ``None`` when completion makes no sense."""
        cur = self.cur
        current: Token | None = None
        prefix = ""
        quote: str | None = None
        for token in tokenize(self.text, self.dialect):
            inside = token.start < cur < token.end
            touching_end = cur == token.end and cur > token.start
            kind = token.kind
            if kind in (TokenKind.LINE_COMMENT, TokenKind.BLOCK_COMMENT):
                open_ended = kind is TokenKind.LINE_COMMENT or not token.terminated
                if inside or (touching_end and open_ended):
                    return None
            elif kind is TokenKind.STRING:
                if inside or (touching_end and not token.terminated):
                    return None
            elif kind is TokenKind.QUOTED_IDENT:
                if inside or (touching_end and not token.terminated):
                    current = token
                    quote = self.text[token.start]
                    prefix = self.text[token.start + 1 : cur]
            elif kind is TokenKind.WORD:
                if token.start < cur <= token.end:
                    current = token
                    prefix = self.text[token.start : cur]
            elif kind in (TokenKind.NUMBER, TokenKind.PARAM, TokenKind.VARIABLE) and (
                token.start < cur <= token.end
            ):
                return None
        return current, prefix, quote

    # ------------------------------------------------------------------ groups and segments

    def _build_groups(self, ci: int) -> None:
        stack: list[int] = []
        self.group_of = [-1] * len(self.sig)
        for i, token in enumerate(self.sig):
            if token.kind is TokenKind.PUNCT and token.text == "(":
                parent = stack[-1] if stack else -1
                following = self.ups[i + 1] if i + 1 < len(self.sig) else ""
                self.groups.append(_Group(i, len(self.sig), parent, following in _QUERY_STARTERS))
                self.group_of[i] = parent
                stack.append(len(self.groups) - 1)
            elif token.kind is TokenKind.PUNCT and token.text == ")" and stack:
                group = stack.pop()
                self.groups[group].close = i
                self.group_of[i] = self.groups[group].parent
            else:
                self.group_of[i] = stack[-1] if stack else -1
        self._close_unfinished(ci)

    def _close_unfinished(self, ci: int) -> None:
        """``count(| FROM t``: an unclosed ``(`` must not swallow the clauses that follow it.

        An open parenthesis that is not a subquery ends where the first clause keyword after the
        cursor begins.
        """
        for number in range(len(self.groups) - 1, -1, -1):
            group = self.groups[number]
            if group.close != len(self.sig) or group.query:
                continue
            for i in range(max(group.open + 1, ci), len(self.sig)):
                if self.group_of[i] == number and self.ups[i] in _CLAUSE_STARTS:
                    group.close = i
                    for j in range(i, len(self.sig)):
                        if self.group_of[j] == number:
                            self.group_of[j] = group.parent
                    for other in self.groups:
                        if other.parent == number and other.open >= i:
                            other.parent = group.parent
                    break

    def _level(self, group: int) -> list[int]:
        """Indices of the tokens that belong directly to ``group`` (-1 = the statement)."""
        if group == -1:
            return [i for i in range(len(self.sig)) if self.group_of[i] == -1]
        g = self.groups[group]
        return [i for i in range(g.open + 1, g.close) if self.group_of[i] == group]

    def _cursor_group(self, ci: int) -> int:
        best = -1
        for number, g in enumerate(self.groups):
            if g.open < ci <= g.close and (best == -1 or g.open > self.groups[best].open):
                best = number
        return best

    def _scope_of(self, group: int) -> int:
        """The nearest enclosing scope: the group itself if it is a subquery, else outwards."""
        while group != -1 and not self.groups[group].query:
            group = self.groups[group].parent
        return group

    def _word(self, level: list[int], k: int) -> str:
        return self.ups[level[k]] if 0 <= k < len(level) else ""

    def _segments(self, group: int) -> list[_Segment]:
        cached = self._segments_cache.get(group)
        if cached is not None:
            return cached
        level = self._level(group)
        segments: list[_Segment] = []
        block = 0
        k = 0
        last = ""
        block_start = 0
        while k < len(level):
            found = self._clause_at(level, k, last, k == block_start)
            if found is None:
                k += 1
                continue
            name, consumed = found
            if segments:
                segments[-1].stop = k
            if name in _SET_OPERATORS:
                block += 1
                block_start = k + consumed
                last = ""
                segments.append(_Segment("", level[k], k + consumed, len(level), block))
            else:
                last = name
                segments.append(_Segment(name, level[k], k + consumed, len(level), block))
            k += consumed
        self._segments_cache[group] = segments
        return segments

    def _clause_at(
        self, level: list[int], k: int, last: str, at_block_start: bool
    ) -> tuple[str, int] | None:
        word = self._word(level, k)
        if not word:
            return None
        nxt = self._word(level, k + 1)
        if word == "WITH" and at_block_start:
            return "WITH", 2 if nxt == "RECURSIVE" else 1
        if word in _SIMPLE_CLAUSES:
            return word, 1
        if word == "GROUP" and nxt == "BY":
            return "GROUP BY", 2
        if word == "ORDER" and nxt == "BY":
            return "ORDER BY", 2
        if word in _JOIN_MODIFIERS or word in ("JOIN", "STRAIGHT_JOIN"):
            j = k
            while self._word(level, j) in _JOIN_MODIFIERS:
                j += 1
            if self._word(level, j) in ("JOIN", "STRAIGHT_JOIN"):
                return "JOIN", j - k + 1
            return None
        if word == "ON":
            if nxt == "CONFLICT":
                return "ON CONFLICT", 2
            if nxt == "DUPLICATE":
                return "ON DUPLICATE", 4 if self._word(level, k + 3) == "UPDATE" else 3
            return ("JOIN ON", 1) if last == "JOIN" else ("ON", 1)
        if word == "INSERT":
            for j in range(k + 1, min(k + 4, len(level))):
                if self._word(level, j) == "INTO":
                    return "INSERT INTO", j - k + 1
            return None
        if word == "REPLACE" and nxt == "INTO":
            return "INSERT INTO", 2
        if word == "UPDATE":
            return "UPDATE", 1
        if word == "DELETE" and nxt == "FROM":
            return "DELETE FROM", 2
        if word == "FOR":
            return "FOR", 2 if nxt in ("UPDATE", "SHARE", "NO", "KEY") else 1
        if word in _SET_OPERATORS:
            return word, 2 if nxt in ("ALL", "DISTINCT") else 1
        return None

    def _segment_at(self, group: int, ci: int) -> tuple[_Segment | None, list[_Segment]]:
        """The segment of ``group`` the cursor index ``ci`` falls in, and its block's segments."""
        segments = self._segments(group)
        current: _Segment | None = None
        for segment in segments:
            if segment.kw < ci:
                current = segment
        if current is None:
            return None, []
        block = [s for s in segments if s.block == current.block]
        return current, block

    # ------------------------------------------------------------------ the description

    def _describe(self, ctx: CursorContext, ci: int) -> None:
        sig, ups = self.sig, self.ups
        group = self._cursor_group(ci)
        scope = self._scope_of(group)
        segment, block = self._segment_at(scope, ci)
        ctx.clause = segment.name if segment else ""
        ctx.group_start = group != -1 and ci == self.groups[group].open + 1

        # sources: the block's own tables first, then those of the enclosing scopes
        ctes = self._visible_ctes(scope)
        ctx.ctes = [self._cte_source(c, 0) for c in ctes]
        if block:
            ctx.sources = self._block_sources(scope, block, ctes, 0)
            ctx.select_aliases = self._select_aliases(scope, block)
        ctx.outer_sources = self._outer_sources(scope, ctes)
        if segment is not None and segment.name == "JOIN ON":
            refs = self._block_refs(scope, block)
            joined = [n for n, r in enumerate(refs) if r.segment == "JOIN" and r.start < segment.kw]
            if joined and joined[-1] < len(ctx.sources):
                ctx.join_target = ctx.sources[joined[-1]]
        ctx.target = self._target(scope, block, ctes)

        prev = sig[ci - 1] if ci > 0 else None
        self._star(ctx, ci, prev, segment)

        if prev is not None and prev.kind is TokenKind.PUNCT and prev.text == ".":
            parts = self._qualifier(ci)
            if parts:
                ctx.expect = Expect.QUALIFIED
                ctx.qualifier = parts
            return

        first = ups[0] if ups else ""
        if self._at_statement_start(ci, first):
            ctx.expect = Expect.STATEMENT
            return
        if first in ("CREATE", "ALTER", "DROP", "TRUNCATE"):
            self._ddl(ctx, ci, group)
            return

        if group != -1 and not self.groups[group].query:
            if self._group_context(ctx, ci, group, scope, segment):
                return
        elif group != -1 and ci == self.groups[group].open + 1:
            ctx.expect = Expect.STATEMENT  # inside "(SELECT" territory: a subquery starts here
            ctx.subquery_only = True
            return

        if prev is not None and prev.text == "::" and prev.kind is TokenKind.OPERATOR:
            ctx.expect = Expect.TYPE
            return
        if self._after_cast_as(ci, group):
            ctx.expect = Expect.TYPE
            return

        if segment is None:
            ctx.expect = Expect.AFTER_OPERAND
            return
        self._by_clause(ctx, ci, scope, segment, prev)

    def _at_statement_start(self, ci: int, first: str) -> bool:
        if ci == 0:
            return True
        if first in _STATEMENT_PREFIXES:
            return all(w in _EXPLAIN_WORDS for w in self.ups[1:ci])
        return False

    def _by_clause(
        self, ctx: CursorContext, ci: int, scope: int, segment: _Segment, prev: Token | None
    ) -> None:
        level = self._level(scope)
        body = [i for i in level[segment.start : segment.stop] if i < ci]
        name = segment.name
        before = len(body)
        last = self.sig[body[-1]] if body else None

        if name in ("FROM", "JOIN", "UPDATE", "DELETE FROM", "INSERT INTO"):
            comma = last is not None and last.text == "," and last.kind is TokenKind.PUNCT
            if before == 0 or (name == "FROM" and comma):
                ctx.expect = Expect.TABLE
            else:
                ctx.expect = Expect.AFTER_OPERAND
            return
        if name == "WITH":
            closing = last is not None and last.text == ")"
            ctx.expect = Expect.STATEMENT if closing else Expect.NOTHING
            return
        if name in ("LIMIT", "OFFSET", "FETCH"):
            ctx.expect = Expect.NOTHING if before == 0 else Expect.AFTER_OPERAND
            return
        if name == "SET" and (before == 0 or (last is not None and last.text == ",")):
            ctx.expect = Expect.SET_TARGET
            ctx.listed = self._assigned(level, segment, ci)
            return
        if name == "VALUES":
            ctx.expect = Expect.AFTER_OPERAND
            return
        ctx.expect = self._operand_state(ci, prev, segment)

    def _operand_state(self, ci: int, prev: Token | None, segment: _Segment | None) -> Expect:
        if prev is None:
            return Expect.OPERAND
        if prev.kind is TokenKind.PUNCT:
            return Expect.OPERAND if prev.text in "(,[" else Expect.AFTER_OPERAND
        if prev.kind is TokenKind.OPERATOR:
            if prev.text == "*" and self._is_star(ci - 1, segment):
                return Expect.AFTER_OPERAND
            return Expect.OPERAND
        if prev.kind is TokenKind.WORD:
            word = prev.upper
            if word in _NOTHING_WORDS:
                return Expect.NOTHING
            if word in _OPERAND_WORDS or word == "FROM":  # FROM here: EXTRACT(YEAR FROM x)
                return Expect.OPERAND
            return Expect.AFTER_OPERAND
        return Expect.AFTER_OPERAND

    def _is_star(self, index: int, segment: _Segment | None) -> bool:
        """Is the ``*`` at ``index`` a select-all (rather than a multiplication)?"""
        if segment is None or segment.name != "SELECT":
            return False
        before = self.sig[index - 1] if index > 0 else None
        if before is None:
            return False
        if before.kind is TokenKind.PUNCT and before.text in ",.":
            return True
        return before.kind is TokenKind.WORD and before.upper in ("SELECT", "DISTINCT", "ALL")

    def _star(
        self, ctx: CursorContext, ci: int, prev: Token | None, segment: _Segment | None
    ) -> None:
        """Remember a ``*`` right before the cursor so it can be expanded to the column list."""
        if ctx.prefix or prev is None or prev.text != "*" or prev.end != self.cur:
            return
        if not self._is_star(ci - 1, segment):
            return
        alias: str | None = None
        start = prev.start
        before = self.sig[ci - 2] if ci >= 2 else None
        if before is not None and before.text == ".":
            parts = self._qualifier(ci - 1)
            if parts:
                alias = parts[-1]
                start = self.sig[ci - 2 - 2 * (len(parts) - 1) - 1].start
        ctx.star = (self.shift + start, self.shift + prev.end, alias)

    def _qualifier(self, ci: int) -> tuple[str, ...]:
        """Names written before the ``.`` at index ``ci - 1``: ``a.b.`` gives ``("a", "b")``."""
        parts: list[str] = []
        j = ci - 1
        while j >= 1 and self.sig[j].text == "." and self.sig[j].kind is TokenKind.PUNCT:
            token = self.sig[j - 1]
            if token.kind is TokenKind.WORD:
                parts.insert(0, token.text)
            elif token.kind is TokenKind.QUOTED_IDENT:
                parts.insert(0, _unquote(token.text))
            else:
                break
            j -= 2
        return tuple(parts)

    def _after_cast_as(self, ci: int, group: int) -> bool:
        if group == -1 or ci == 0 or self.ups[ci - 1] != "AS":
            return False
        opener = self.groups[group].open
        return opener > 0 and self.ups[opener - 1] == "CAST"

    # ------------------------------------------------------------------ groups of columns

    def _listed_in_group(self, group: int, ci: int) -> tuple[str, ...]:
        """Names already written in the parenthesised list ``group`` before the cursor."""
        names = []
        for i in self._level(group):
            if i >= ci:
                break
            name = self._name_of(self.sig[i])
            if name:
                names.append(name)
        return tuple(names)

    def _assigned(self, level: list[int], segment: _Segment, ci: int) -> tuple[str, ...]:
        """Columns already assigned earlier in this ``SET`` clause (``name = ...``)."""
        names = []
        positions = [i for i in level[segment.start : segment.stop] if i < ci]
        for before, here in pairwise(positions):
            if self.sig[here].text == "=" and self.sig[here].kind is TokenKind.OPERATOR:
                name = self._name_of(self.sig[before])
                if name:
                    names.append(name)
        return tuple(names)

    def _group_context(
        self, ctx: CursorContext, ci: int, group: int, scope: int, segment: _Segment | None
    ) -> bool:
        """Handle the parentheses that are not subqueries; ``True`` when the context is decided."""
        g = self.groups[group]
        parent_scope = self._scope_of(g.parent)
        parent_segment, _ = self._segment_at(parent_scope, g.open + 1)
        name = parent_segment.name if parent_segment else ""
        direct = g.parent == parent_scope or g.parent == -1
        pre = self.ups[g.open - 1] if g.open > 0 else ""
        if direct and name in ("INSERT INTO", "ON CONFLICT", "USING"):
            ctx.expect = Expect.COLUMN_LIST
            ctx.listed = self._listed_in_group(group, ci)
            return True
        if direct and name == "VALUES":
            ctx.in_values = True
            ctx.expect = (
                Expect.OPERAND
                if self._operand_state(ci, self.sig[ci - 1], None) is Expect.OPERAND
                else Expect.AFTER_OPERAND
            )
            return True
        if name == "WITH" and pre == "AS":
            ctx.expect = Expect.STATEMENT
            ctx.subquery_only = True
            return True
        if direct and name in ("FROM", "JOIN") and ci == g.open + 1:
            ctx.expect = Expect.STATEMENT
            ctx.subquery_only = True
            return True
        return False

    # ------------------------------------------------------------------ sources

    def _parse_ref(
        self, level: list[int], pos: int, stop: int, segment: str
    ) -> tuple[_Ref | None, int]:
        k = pos
        while k < stop and self._word(level, k) in ("LATERAL", "ONLY"):
            k += 1
        if k >= stop:
            return None, k
        token = self.sig[level[k]]
        parts: list[str] = []
        derived = -1
        start = level[k]
        if token.kind is TokenKind.PUNCT and token.text == "(":
            derived = next((n for n, g in enumerate(self.groups) if g.open == level[k]), -1)
            k += 1
            if k < stop and self.sig[level[k]].text == ")":
                k += 1
        elif token.kind in (TokenKind.WORD, TokenKind.QUOTED_IDENT):
            while k < stop:
                token = self.sig[level[k]]
                if token.kind is TokenKind.WORD:
                    parts.append(token.text)
                elif token.kind is TokenKind.QUOTED_IDENT:
                    parts.append(_unquote(token.text))
                else:
                    break
                k += 1
                if k < stop and self.sig[level[k]].text == "." and k + 1 < stop:
                    k += 1
                    continue
                break
            if k < stop and self.sig[level[k]].text == ".":  # "schema." still being typed
                return None, k + 1
            is_call = k < stop and self.sig[level[k]].text == "(" and segment != "INSERT INTO"
            if is_call:  # a table function: nothing to resolve
                k += 1
                if k < stop and self.sig[level[k]].text == ")":
                    k += 1
                parts = []
        else:
            return None, k
        alias: str | None = None
        if k < stop and self._word(level, k) == "AS":
            k += 1
        if k < stop:
            token = self.sig[level[k]]
            if token.kind is TokenKind.QUOTED_IDENT:
                alias = _unquote(token.text)
                k += 1
            elif (
                token.kind is TokenKind.WORD
                and token.upper not in _NOT_ALIAS
                and (
                    token.upper not in self.dialect.reserved_words
                    or self._word(level, k - 1) == "AS"
                )
            ):
                alias = token.text
                k += 1
        if not parts and derived == -1 and alias is None:
            return None, k
        return _Ref(tuple(parts), alias, derived, segment, start), k

    def _block_refs(self, scope: int, block: list[_Segment]) -> list[_Ref]:
        level = self._level(scope)
        refs: list[_Ref] = []
        selecting = any(segment.name == "SELECT" for segment in block)
        for segment in block:
            name = segment.name
            if name not in ("FROM", "JOIN", "UPDATE", "DELETE FROM", "INSERT INTO"):
                continue
            if name == "INSERT INTO" and selecting:
                continue  # INSERT ... SELECT: the target is not a source of the query
            pos, stop = segment.start, segment.stop
            while pos < stop:
                ref, pos = self._parse_ref(level, pos, stop, name)
                if ref is not None:
                    refs.append(ref)
                if name != "FROM" or pos >= stop or self.sig[level[pos]].text != ",":
                    break
                pos += 1
        return refs

    def _block_sources(
        self, scope: int, block: list[_Segment], ctes: list[tuple[int, _Cte]], depth: int
    ) -> list[Source]:
        sources = []
        for ref in self._block_refs(scope, block):
            sources.append(self._resolve(ref, ctes, depth))
        return sources

    def _resolve(self, ref: _Ref, ctes: list[tuple[int, _Cte]], depth: int) -> Source:
        if ref.derived != -1:
            columns = self._group_columns(ref.derived, ctes, depth + 1)
            return Source(ref.alias, "", None, columns, "derived")
        if not ref.parts:
            return Source(ref.alias, "", None, (), "unknown")
        name = ref.parts[-1]
        if len(ref.parts) == 1:
            for _, cte in ctes:
                if cte.name.casefold() == name.casefold():
                    source = self._cte_source((_, cte), depth)
                    return Source(ref.alias, cte.name, None, source.columns, "cte")
        table = self.index.table(ref.parts) if self.index else None
        if table is None:
            return Source(ref.alias, name, None, (), "unknown")
        return Source(
            ref.alias,
            table.name,
            table,
            _table_columns(table),
            "view" if table.is_view else "table",
        )

    def _target(
        self, scope: int, block: list[_Segment], ctes: list[tuple[int, _Cte]]
    ) -> Source | None:
        """The table of ``INSERT INTO`` / ``UPDATE`` / ``DELETE FROM`` in this block."""
        level = self._level(scope)
        for segment in block:
            if segment.name in ("INSERT INTO", "UPDATE", "DELETE FROM"):
                ref, _ = self._parse_ref(level, segment.start, segment.stop, segment.name)
                if ref is not None:
                    return self._resolve(ref, ctes, 0)
        return None

    def _outer_sources(self, scope: int, ctes: list[tuple[int, _Cte]]) -> list[Source]:
        outer: list[Source] = []
        group = scope
        while group != -1:
            parent = self._scope_of(self.groups[group].parent)
            _, block = self._segment_at(parent, self.groups[group].open + 1)
            if block:
                outer.extend(self._block_sources(parent, block, ctes, 0))
            group = parent
        return outer

    def _select_aliases(self, scope: int, block: list[_Segment]) -> list[str]:
        level = self._level(scope)
        names: list[str] = []
        for segment in block:
            if segment.name != "SELECT":
                continue
            for item in self._items(level, segment.start, segment.stop):
                alias = self._item_alias(item)
                if alias:
                    names.append(alias)
        return names

    # ------------------------------------------------------------------ select items

    def _items(self, level: list[int], start: int, stop: int) -> list[list[int]]:
        """Split level positions ``start:stop`` on top-level commas into lists of token indices."""
        items: list[list[int]] = [[]]
        for k in range(start, stop):
            index = level[k]
            token = self.sig[index]
            if token.kind is TokenKind.PUNCT and token.text == ",":
                items.append([])
            else:
                items[-1].append(index)
        return [i for i in items if i]

    def _item_alias(self, item: list[int]) -> str | None:
        """The explicit alias of a select-list item (``x AS name`` or ``x name``)."""
        if len(item) < 2:
            return None
        last = self.sig[item[-1]]
        before = self.sig[item[-2]]
        if before.kind is TokenKind.WORD and before.upper == "AS":
            return self._name_of(last)
        if (
            last.kind is TokenKind.QUOTED_IDENT
            and before.kind is not TokenKind.OPERATOR
            and before.text not in ".,("
        ):
            return _unquote(last.text)
        if (
            last.kind is TokenKind.WORD
            and last.upper not in self.dialect.reserved_words
            and last.upper not in _NOT_ALIAS
            and before.kind
            in (TokenKind.WORD, TokenKind.QUOTED_IDENT, TokenKind.NUMBER, TokenKind.STRING)
        ) or (
            last.kind is TokenKind.WORD
            and before.text == ")"
            and last.upper not in self.dialect.reserved_words
        ):
            return last.text
        return None

    def _name_of(self, token: Token) -> str | None:
        if token.kind is TokenKind.WORD:
            return token.text
        if token.kind is TokenKind.QUOTED_IDENT:
            return _unquote(token.text)
        return None

    def _item_name(self, item: list[int]) -> str | None:
        """Output column name of a select-list item, ``None`` for an unnamed expression."""
        alias = self._item_alias(item)
        if alias:
            return alias
        tokens = [self.sig[i] for i in item]
        if tokens and all(
            t.kind in (TokenKind.WORD, TokenKind.QUOTED_IDENT) or t.text == "." for t in tokens
        ):
            return self._name_of(tokens[-1])
        return None

    # ------------------------------------------------------------------ derived tables and CTEs

    def _group_columns(
        self, group: int, ctes: list[tuple[int, _Cte]], depth: int
    ) -> tuple[SourceColumn, ...]:
        """Output columns of the subquery in ``group``."""
        if depth > _MAX_DEPTH:
            return ()
        g = self.groups[group]
        names = self._sqlglot_names(g) if g.close < len(self.sig) else None
        if names is not None:
            return tuple(SourceColumn(n) for n in names)
        blocks = self._segments(group)
        first = [s for s in blocks if s.block == (blocks[0].block if blocks else 0)]
        if not first:
            return ()
        level = self._level(group)
        select = next((s for s in first if s.name == "SELECT"), None)
        if select is None:
            return ()
        sources: list[Source] | None = None
        columns: list[SourceColumn] = []
        for item in self._items(level, select.start, select.stop):
            tokens = [self.sig[i] for i in item]
            star = tokens[-1].text == "*" and tokens[-1].kind is TokenKind.OPERATOR
            if star:
                if sources is None:
                    sources = self._block_sources(group, first, ctes, depth)
                wanted = tokens[-3].text.casefold() if len(tokens) >= 3 else None
                for source in sources:
                    if wanted is None or wanted in (
                        (source.alias or "").casefold(),
                        source.name.casefold(),
                    ):
                        columns.extend(source.columns)
                continue
            name = self._item_name(item)
            if name:
                columns.append(SourceColumn(name))
        return tuple(columns)

    def _sqlglot_names(self, g: _Group) -> list[str] | None:
        """Output names of a finished subquery as ``sqlglot`` reads them (``None`` if unsure)."""
        text = self.text[self.sig[g.open].end : self.sig[g.close].start]
        if not text.strip():
            return None
        try:
            tree = sqlglot.parse_one(text, read=self.dialect.sqlglot_name)
        except SqlglotError:
            return None
        names = getattr(tree, "named_selects", None)
        if not names or any("*" in n for n in names):
            return None
        return [n for n in names if n]

    def _visible_ctes(self, scope: int) -> list[tuple[int, _Cte]]:
        """CTEs defined by ``WITH`` clauses of the scope and of every enclosing scope."""
        found: list[tuple[int, _Cte]] = []
        group = scope
        while True:
            for segment in self._segments(group):
                if segment.name == "WITH":
                    found.extend((group, cte) for cte in self._parse_ctes(group, segment))
            if group == -1:
                break
            group = self._scope_of(self.groups[group].parent)
        return found

    def _parse_ctes(self, group: int, segment: _Segment) -> list[_Cte]:
        level = self._level(group)
        ctes: list[_Cte] = []
        k = segment.start
        while k < segment.stop:
            token = self.sig[level[k]]
            if token.kind not in (TokenKind.WORD, TokenKind.QUOTED_IDENT):
                k += 1
                continue
            name = self._name_of(token) or ""
            k += 1
            columns: list[str] = []
            if k < segment.stop and self.sig[level[k]].text == "(":
                inner = next((n for n, g in enumerate(self.groups) if g.open == level[k]), -1)
                if inner != -1 and not self.groups[inner].query:
                    columns = [
                        n for i in self._items(self._level(inner), 0, len(self._level(inner)))
                        if (n := self._item_name(i))
                    ]  # fmt: skip
                k += 2 if k + 1 < segment.stop and self.sig[level[k + 1]].text == ")" else 1
            while k < segment.stop and self._word(level, k) in ("AS", "NOT", "MATERIALIZED"):
                k += 1
            body = -1
            if k < segment.stop and self.sig[level[k]].text == "(":
                body = next((n for n, g in enumerate(self.groups) if g.open == level[k]), -1)
                k += 2 if k + 1 < segment.stop and self.sig[level[k + 1]].text == ")" else 1
            if body != -1:
                ctes.append(_Cte(name, tuple(columns), body))
            while k < segment.stop and self.sig[level[k]].text != ",":
                k += 1
            k += 1
        return ctes

    def _cte_source(self, entry: tuple[int, _Cte], depth: int) -> Source:
        _, cte = entry
        key = (cte.body, cte.name.casefold())
        cached = self._cte_cache.get(key)
        if cached is not None:
            return cached
        if key in self._cte_busy or depth > _MAX_DEPTH:
            return Source(None, cte.name, None, (), "cte")
        self._cte_busy.add(key)
        try:
            if cte.columns:
                columns = tuple(SourceColumn(n) for n in cte.columns)
            else:
                inner_ctes = self._visible_ctes(cte.body)
                columns = self._group_columns(cte.body, inner_ctes, depth + 1)
        finally:
            self._cte_busy.discard(key)
        source = Source(None, cte.name, None, columns, "cte")
        self._cte_cache[key] = source
        return source

    # ------------------------------------------------------------------ DDL

    def _ddl(self, ctx: CursorContext, ci: int, group: int) -> None:
        words = self.ups[:ci]
        first, last = words[0], words[-1]
        tokens = self.sig[:ci]
        if first == "TRUNCATE":
            ctx.expect = (
                Expect.TABLE if ci <= 2 and last in ("TRUNCATE", "TABLE") else Expect.NOTHING
            )
        elif first == "DROP":
            if (last == "EXISTS" and ("TABLE" in words or "VIEW" in words)) or last in (
                "TABLE",
                "VIEW",
            ):
                ctx.expect = Expect.TABLE
            elif ci == 1:
                ctx.expect = Expect.AFTER_OPERAND
            else:
                ctx.expect = Expect.NOTHING
        elif first == "ALTER" and "TABLE" in words:
            self._ddl_alter(ctx, ci, words, tokens)
        elif first == "CREATE":
            self._ddl_create(ctx, ci, group, words, tokens)
        else:
            ctx.expect = Expect.AFTER_OPERAND

    def _ddl_target(self, tokens: list[Token], after: int) -> Source | None:
        """The table named after the word at ``after`` (``ALTER TABLE <t>``, ``... ON <t>``)."""
        parts: list[str] = []
        j = after + 1
        while j < len(tokens) and tokens[j].kind in (TokenKind.WORD, TokenKind.QUOTED_IDENT):
            if tokens[j].upper in ("IF", "EXISTS", "ONLY") and not parts:
                j += 1
                continue
            name = self._name_of(tokens[j])
            if name:
                parts.append(name)
            if j + 1 < len(tokens) and tokens[j + 1].text == ".":
                j += 2
                continue
            break
        if not parts:
            return None
        table = self.index.table(parts) if self.index else None
        if table is None:
            return Source(None, parts[-1], None, (), "unknown")
        return Source(None, table.name, table, _table_columns(table), "table")

    def _ddl_alter(
        self, ctx: CursorContext, ci: int, words: list[str], tokens: list[Token]
    ) -> None:
        position = words.index("TABLE")
        ctx.target = self._ddl_target(tokens, position)
        last = words[-1] if words else ""
        if last == "TABLE" or (last in ("EXISTS", "ONLY") and position < ci - 1):
            ctx.expect = Expect.TABLE
        elif last == "COLUMN" and any(w in ("DROP", "ALTER", "RENAME") for w in words[position:]):
            ctx.expect = Expect.TARGET_COLUMN
        elif last == "COLUMN" or (len(words) >= 2 and words[-2] == "COLUMN" and "ADD" in words):
            ctx.expect = Expect.TYPE if last != "COLUMN" else Expect.NOTHING
        else:
            ctx.expect = Expect.AFTER_OPERAND

    def _ddl_create(
        self, ctx: CursorContext, ci: int, group: int, words: list[str], tokens: list[Token]
    ) -> None:
        last = words[-1] if words else ""
        if "INDEX" in words and "ON" in words:
            on = len(words) - 1 - words[::-1].index("ON")
            ctx.target = self._ddl_target(tokens, on)
            inside = group != -1 and not self.groups[group].query
            if last == "ON" and not inside:
                ctx.expect = Expect.TABLE
            elif inside:
                ctx.expect = Expect.COLUMN_LIST
                ctx.listed = self._listed_in_group(group, ci)
            else:
                ctx.expect = Expect.AFTER_OPERAND
            return
        if group != -1 and "TABLE" in words:
            g = self.groups[group]
            level = [i for i in range(g.open + 1, ci) if self.group_of[i] == group]
            commas = [n for n, i in enumerate(level) if tokens[i].text == ","]
            level = level[commas[-1] + 1 :] if commas else level  # the definition being typed
            if len(level) == 0:
                ctx.expect = Expect.NOTHING  # a new column name
            elif len(level) == 1 and tokens[level[0]].kind in (
                TokenKind.WORD,
                TokenKind.QUOTED_IDENT,
            ):
                ctx.expect = Expect.TYPE
            elif last == "REFERENCES":
                ctx.expect = Expect.TABLE
            else:
                ctx.expect = Expect.AFTER_OPERAND
            return
        if last == "REFERENCES":
            ctx.expect = Expect.TABLE
            return
        if last in (
            "CREATE",
            "TEMP",
            "TEMPORARY",
            "UNIQUE",
            "MATERIALIZED",
            "OR",
            "REPLACE",
            "VIRTUAL",
        ):
            ctx.expect = Expect.AFTER_OPERAND
            return
        if last in ("TABLE", "VIEW", "INDEX", "TRIGGER", "SCHEMA", "DATABASE", "EXISTS") and (
            "SELECT" not in words
        ):
            ctx.expect = Expect.NOTHING  # a new name
            return
        if "AS" in words and "VIEW" in words:
            ctx.expect = Expect.STATEMENT
            return
        ctx.expect = Expect.AFTER_OPERAND


@dataclass(frozen=True, slots=True)
class _Cte:
    name: str
    columns: tuple[str, ...]
    body: int  # group index of the CTE's query


def _table_columns(table: Table) -> tuple[SourceColumn, ...]:
    foreign = table.foreign_key_columns
    return tuple(
        SourceColumn(c.name, c.type, c.primary_key, c.name in foreign, c.nullable, c.comment)
        for c in table.columns
    )


def _unquote(text: str) -> str:
    """``"a""b"`` -> ``a"b``; ```x``` -> ``x``; ``[x]`` -> ``x``."""
    if not text:
        return text
    opener = text[0]
    closer = "]" if opener == "[" else opener
    inner = text[1:-1] if len(text) > 1 and text[-1] == closer else text[1:]
    return inner.replace(closer * 2, closer) if opener != "[" else inner


__all__ = [
    "CursorContext",
    "Expect",
    "Source",
    "SourceColumn",
    "analyze",
]

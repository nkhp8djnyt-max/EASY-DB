"""Turn a cursor context into ranked suggestions.

Order of the list: first what the *context* asks for (columns of the tables in ``FROM`` before
functions before keywords, tables related by a foreign key first after ``JOIN``), then how well the
text typed so far matches (prefix, word start, substring, letters in order, and a typo tolerant
fuzzy match by ``rapidfuzz``), then how often the user accepted the suggestion before, then
alphabetically.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import partial

from rapidfuzz import fuzz, process

from ..dialects import Dialect, DialectId
from ..dialects.vocab import FunctionCategory, FunctionSpec
from ..schema import DatabaseSchema, ForeignKey, Table
from . import keywords as kw
from .context import CursorContext, Expect, Source, SourceColumn, analyze
from .index import SchemaIndex
from .messages import t
from .snippets import CURSOR, Where, snippets_for


class KeywordCase(StrEnum):
    UPPER = "upper"
    LOWER = "lower"
    #: Follow the case of what the user has typed (upper case when nothing is typed yet).
    PRESERVE = "preserve"


class Kind(StrEnum):
    KEYWORD = "keyword"
    TABLE = "table"
    VIEW = "view"
    CTE = "cte"
    SCHEMA = "schema"
    COLUMN = "column"
    ALIAS = "alias"
    FUNCTION = "function"
    TYPE = "type"
    SNIPPET = "snippet"
    #: A ready ``JOIN ... ON`` condition made from a foreign key.
    JOIN = "join"
    #: "Expand ``*`` to the column list".
    STAR = "star"


@dataclass(frozen=True, slots=True)
class Completion:
    #: Text shown in the list and matched against what was typed.
    label: str
    #: Text that replaces the word under the cursor (or ``replace`` when given).
    insert: str
    kind: Kind
    #: Right-hand text of the row: a column's type, a function's signature, a table's size.
    detail: str = ""
    #: Longer description for the details panel.
    doc: str = ""
    #: Move the cursor back this many characters after inserting (``COUNT(|)``).
    cursor_back: int = 0
    #: Open the list again right after accepting (``schema.`` -> its tables, ``alias.`` -> columns).
    retrigger: bool = False
    #: Identifies the suggestion for the "how often accepted" ranking.
    usage_key: str = ""
    #: Range to replace instead of the word under the cursor (star expansion).
    replace: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class Completions:
    items: tuple[Completion, ...]
    replace_start: int
    replace_end: int
    prefix: str
    expect: Expect


EMPTY = Completions((), 0, 0, "", Expect.NOTHING)


@dataclass(slots=True)
class _Cand:
    label: str
    insert: str
    kind: Kind
    tier: int
    detail: str = ""
    doc: Callable[[], str] | str = ""
    hint: int = 0
    usage: str = ""
    cursor_back: int = 0
    retrigger: bool = False
    replace: tuple[int, int] | None = None
    #: What typed text is matched against when it differs from the label (``o.id`` -> ``id``).
    match: str = ""


# ---------------------------------------------------------------------- the completer


class Completer:
    """Stateless apart from a one-schema index cache; safe to call from a worker thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: tuple[DatabaseSchema, SchemaIndex] | None = None

    def index_for(self, schema: DatabaseSchema | None) -> SchemaIndex | None:
        if schema is None:
            return None
        with self._lock:
            if self._cached is not None and self._cached[0] is schema:
                return self._cached[1]
        index = SchemaIndex(schema)
        with self._lock:
            self._cached = (schema, index)
        return index

    def complete(
        self,
        sql: str,
        offset: int,
        dialect: Dialect,
        schema: DatabaseSchema | None = None,
        *,
        forced: bool = False,
        keyword_case: KeywordCase = KeywordCase.UPPER,
        frequency: Callable[[str], int] | None = None,
        limit: int = 100,
    ) -> Completions:
        index = self.index_for(schema)
        ctx = analyze(sql, offset, dialect, index)
        if ctx.expect is Expect.NOTHING:
            return Completions((), ctx.replace_start, ctx.replace_end, ctx.prefix, ctx.expect)
        after = sql[ctx.replace_end : ctx.replace_end + 1]
        builder = _Builder(ctx, dialect, index, keyword_case, forced, after)
        candidates = builder.build()
        ranked = _rank(candidates, ctx.prefix, frequency or (lambda _key: 0))[:limit]
        items = tuple(
            Completion(
                label=c.label,
                insert=c.insert,
                kind=c.kind,
                detail=c.detail,
                doc=c.doc() if callable(c.doc) else c.doc,
                cursor_back=c.cursor_back,
                retrigger=c.retrigger,
                usage_key=c.usage,
                replace=c.replace,
            )
            for c in ranked
        )
        return Completions(items, ctx.replace_start, ctx.replace_end, ctx.prefix, ctx.expect)


# ---------------------------------------------------------------------- ranking


def _word_starts(label: str) -> list[int]:
    starts = []
    for i, ch in enumerate(label):
        if not ch.isalnum():
            continue
        if i == 0 or not label[i - 1].isalnum() or (ch.isupper() and label[i - 1].islower()):
            starts.append(i)
    return starts


def quality(needle: str, label: str) -> int:
    """How well ``needle`` (case folded) matches ``label``: 5 exact, 4 prefix, 3 word start,
    2 inside (from three letters on), 1 letters in order, -1 no match."""
    low = label.casefold()
    if low == needle and needle:
        return 5
    if low.startswith(needle):
        return 4
    if any(low.startswith(needle, i) for i in _word_starts(label)):
        return 3
    if needle in low:
        return 2 if len(needle) >= 3 else -1  # one or two letters inside a word are only noise
    if len(needle) < 3 or needle[0] != low[0]:
        return -1  # letters-in-order is for abbreviations like "ordit" -> "order_items"
    position = 0
    for ch in needle:
        position = low.find(ch, position) + 1
        if position == 0:
            return -1
    return 1


def _rank(candidates: list[_Cand], prefix: str, frequency: Callable[[str], int]) -> list[_Cand]:
    scored: list[tuple[_Cand, int]] = []
    if not prefix:
        scored = [(c, 0) for c in candidates]
    else:
        needle = prefix.casefold()
        missed: list[_Cand] = []
        for candidate in candidates:
            q = quality(needle, candidate.label)
            if candidate.match:
                q = max(q, quality(needle, candidate.match))
            if q >= 0:
                scored.append((candidate, q))
            else:
                missed.append(candidate)
        if len(needle) >= 3 and missed:  # typo tolerance: "custmer" still finds "customer"
            names = [c.match or c.label for c in missed]
            for _name, _score, position in process.extract(
                prefix,
                names,
                scorer=fuzz.WRatio,
                processor=str.casefold,
                score_cutoff=82,
                limit=None,
            ):
                scored.append((missed[position], 0))

    def key(pair: tuple[_Cand, int]) -> tuple[int, int, int, int, str]:
        candidate, q = pair
        tier, hint = candidate.tier, candidate.hint
        if prefix and candidate.kind is Kind.SNIPPET and q < 5:
            tier, hint = max(tier, 1), hint + 1000  # only an exact trigger outranks the keywords
        uses = frequency(candidate.usage) if candidate.usage else 0
        return (tier, -q, -uses, hint, candidate.label.casefold())

    scored.sort(key=key)
    return [candidate for candidate, _ in scored]


# ---------------------------------------------------------------------- candidate builder


def _apply_case(word: str, mode: KeywordCase, prefix: str) -> str:
    if mode is KeywordCase.UPPER:
        return word.upper()
    if mode is KeywordCase.LOWER:
        return word.lower()
    letters = [c for c in prefix if c.isalpha()]
    return word.lower() if letters and all(c.islower() for c in letters) else word.upper()


class _Builder:
    def __init__(
        self,
        ctx: CursorContext,
        dialect: Dialect,
        index: SchemaIndex | None,
        keyword_case: KeywordCase,
        forced: bool,
        after: str,
    ) -> None:
        self.ctx = ctx
        self.dialect = dialect
        self.index = index
        self.mode = keyword_case
        self.forced = forced
        self.after = after
        self.default_schema = index.schema.default_schema if index else ""

    # ------------------------------------------------------------------ helpers

    def _case(self, word: str) -> str:
        return _apply_case(word, self.mode, self.ctx.prefix)

    def _ident(self, name: str) -> str:
        if self.ctx.quote is not None:
            return self.dialect.quote_ident(name)
        return self.dialect.quote_ident_if_needed(name)

    def _keyword(self, word: str, tier: int, hint: int = 0) -> _Cand:
        text = self._case(word)
        trailing = "" if self.after.isspace() else " "
        return _Cand(text, text + trailing, Kind.KEYWORD, tier, hint=hint, usage=f"keyword:{word}")

    def _table_ref(self, table: Table) -> str:
        if table.schema == self.default_schema or not self.default_schema:
            return self._ident(table.name)
        return f"{self._ident(table.schema)}.{self._ident(table.name)}"

    def _quoted_only(self) -> bool:
        return self.ctx.quote is not None

    # ------------------------------------------------------------------ dispatch

    def build(self) -> list[_Cand]:
        expect = self.ctx.expect
        if self._quoted_only() and expect not in (
            Expect.TABLE,
            Expect.OPERAND,
            Expect.QUALIFIED,
            Expect.COLUMN_LIST,
            Expect.SET_TARGET,
            Expect.TARGET_COLUMN,
        ):
            return []
        if expect is Expect.STATEMENT:
            return self._statement()
        if expect is Expect.TABLE:
            return self._tables()
        if expect is Expect.OPERAND:
            return self._operand()
        if expect is Expect.AFTER_OPERAND:
            return self._after_operand()
        if expect is Expect.QUALIFIED:
            return self._qualified()
        if expect in (Expect.COLUMN_LIST, Expect.SET_TARGET, Expect.TARGET_COLUMN):
            return self._target_columns()
        if expect is Expect.TYPE:
            return self._types()
        return []

    # ------------------------------------------------------------------ statement start

    def _statement(self) -> list[_Cand]:
        ctx = self.ctx
        if self._quoted_only():
            return []
        cands: list[_Cand] = []
        if ctx.subquery_only or ctx.group_start:
            words = kw.allowed(kw.QUERY_STARTERS, self.dialect)
            if self.dialect.id is not DialectId.POSTGRESQL:
                words = tuple(w for w in words if w != "TABLE")
            return [self._keyword(w, 0, i) for i, w in enumerate(words)]
        cands.extend(self._snippets(Where.STATEMENT, tier=0))
        for i, word in enumerate(kw.statement_keywords(self.dialect)):
            cands.append(self._keyword(word, 1, i))
        return cands

    def _snippets(self, where: Where, tier: int) -> list[_Cand]:
        out = []
        for s in snippets_for(where, self.dialect):
            body = self._snippet_text(s.body)
            before, _, after = body.partition(CURSOR)
            out.append(
                _Cand(
                    s.trigger,
                    before + after,
                    Kind.SNIPPET,
                    tier,
                    detail=s.label,
                    doc=t(s.description) if s.description else s.label,
                    usage=f"snippet:{s.trigger}",
                    cursor_back=len(after),
                )
            )
        return out

    def _snippet_text(self, body: str) -> str:
        placeholder = "\x00"
        text = body.replace(CURSOR, placeholder)
        if self.mode is KeywordCase.LOWER:
            text = text.lower()
        elif self.mode is KeywordCase.PRESERVE:
            letters = [c for c in self.ctx.prefix if c.isalpha()]
            if letters and all(c.islower() for c in letters):
                text = text.lower()
        return text.replace(placeholder, CURSOR)

    # ------------------------------------------------------------------ tables

    def _tables(self) -> list[_Cand]:
        ctx = self.ctx
        join = ctx.clause == "JOIN"
        local = [s.table for s in ctx.sources if s.table is not None]
        related = self.index.related_to(local) if join and self.index else set()
        cands: list[_Cand] = []
        for cte in ctx.ctes:
            cands.append(
                _Cand(
                    cte.name,
                    self._ident(cte.name),
                    Kind.CTE,
                    0,
                    detail="CTE · " + t("{n} cols", n=len(cte.columns)),
                    doc=t("common table expression of this statement"),
                    usage=f"cte:{cte.name}",
                )
            )
        if self.index is None:
            return cands
        for table in self.index.schema.tables:
            default = table.schema == self.default_schema
            tier = 1 if default else 2
            doc: Callable[[], str] | str = _table_doc(table)
            if table.key in related:
                tier = 0
                doc = self._relation_doc(table, local)
            label = table.name if default else f"{table.schema}.{table.name}"
            cands.append(
                _Cand(
                    label,
                    self._table_ref(table),
                    Kind.VIEW if table.is_view else Kind.TABLE,
                    tier,
                    detail=t("{n} cols", n=len(table.columns))
                    + (" · " + t("view") if table.is_view else ""),
                    doc=doc,
                    usage=f"table:{table.key}",
                )
            )
        if len(self.index.schema.schemas) > 1:
            for name in self.index.schema.schemas:
                cands.append(
                    _Cand(
                        name,
                        self._ident(name) + ".",
                        Kind.SCHEMA,
                        3,
                        detail=t("schema"),
                        retrigger=True,
                        usage=f"schema:{name}",
                    )
                )
        return cands

    def _relation_doc(self, table: Table, local: list[Table]) -> Callable[[], str]:
        def doc() -> str:
            lines = []
            for other in local:
                for fk, holder in SchemaIndex.foreign_keys_between(table, other):
                    lines.append(_fk_text(fk, holder))
            return "\n".join(lines)

        return doc

    # ------------------------------------------------------------------ expressions

    def _operand(self) -> list[_Cand]:
        ctx = self.ctx
        if ctx.prev_word == "IS":
            return [
                self._keyword(w, 0, i) for i, w in enumerate(kw.allowed(kw.AFTER_IS, self.dialect))
            ]
        cands: list[_Cand] = []
        if ctx.in_values:
            cands.extend(self._keyword(w, 0, i) for i, w in enumerate(kw.VALUE_KEYWORDS))
            cands.extend(self._functions(0))
            return cands
        if ctx.join_target is not None and ctx.prev_word == "ON":
            cands.extend(self._join_conditions())
        cands.extend(self._columns(ctx.sources, tier=0, qualify_always=False))
        if ctx.clause in ("ORDER BY", "GROUP BY", "HAVING"):
            for alias in ctx.select_aliases:
                cands.append(
                    _Cand(
                        alias,
                        self._ident(alias),
                        Kind.ALIAS,
                        0,
                        detail=t("output column"),
                        usage=f"alias:{alias}",
                    )
                )
        if self._quoted_only():
            return cands
        for source in ctx.sources:
            if source.ref:
                cands.append(self._alias_item(source, tier=1))
        if not ctx.sources and len(ctx.prefix) >= 2:
            cands.extend(self._any_column())
        for source in ctx.outer_sources:
            if source.ref:
                cands.append(self._alias_item(source, tier=2))
        cands.extend(self._columns(ctx.outer_sources, tier=2, qualify_always=True))
        words = list(kw.OPERAND_KEYWORDS)
        if ctx.clause == "SELECT" and ctx.prev_word in ("SELECT", ""):
            words = ["DISTINCT", "ALL", *words]
        cands.extend(self._keyword(w, 4, i) for i, w in enumerate(words))
        cands.extend(self._functions(4))
        if (
            ctx.prev_word == "("
            or ctx.group_start
            or ctx.prev_word in ("IN", "ANY", "ALL", "SOME", "EXISTS")
        ):
            cands.append(self._keyword("SELECT", 1, -1))  # a subquery may start here
        return cands

    def _alias_item(self, source: Source, tier: int) -> _Cand:
        what = source.name or t("subquery")
        return _Cand(
            source.ref,
            self._ident(source.ref) + ".",
            Kind.ALIAS,
            tier,
            detail=what,
            doc=t("alias of {name}", name=what) if source.alias else t("table {name}", name=what),
            retrigger=True,
            usage=f"alias:{source.ref}",
        )

    def _columns(self, sources: list[Source], tier: int, *, qualify_always: bool) -> list[_Cand]:
        counts: dict[str, int] = {}
        for source in sources:
            for column in source.columns:
                counts[column.name.casefold()] = counts.get(column.name.casefold(), 0) + 1
        cands = []
        for source in sources:
            for column in source.columns:
                ambiguous = counts[column.name.casefold()] > 1 or qualify_always
                qualified = ambiguous and bool(source.ref) and not self._quoted_only()
                label = f"{source.ref}.{column.name}" if qualified else column.name
                insert = (
                    f"{self._ident(source.ref)}.{self._ident(column.name)}"
                    if qualified
                    else self._ident(column.name)
                )
                cands.append(
                    _Cand(
                        label,
                        insert,
                        Kind.COLUMN,
                        tier,
                        detail=column.type or (source.name or ""),
                        doc=_column_doc(source, column),
                        usage=_column_key(source, column),
                        match=column.name,
                    )
                )
        return cands

    def _any_column(self) -> list[_Cand]:
        """No ``FROM`` yet: offer columns of every table so typing a name still helps."""
        if self.index is None:
            return []
        cands = []
        for table in self.index.schema.tables:
            foreign = table.foreign_key_columns
            for column in table.columns:
                source = Source(None, table.name, table, (), "table")
                cands.append(
                    _Cand(
                        column.name,
                        self._ident(column.name),
                        Kind.COLUMN,
                        6,
                        detail=f"{table.name} · {column.type}",
                        doc=_column_doc(
                            source,
                            SourceColumn(
                                column.name,
                                column.type,
                                column.primary_key,
                                column.name in foreign,
                                column.nullable,
                                column.comment,
                            ),
                        ),
                        usage=f"column:{table.key}.{column.name}",
                    )
                )
        return cands

    def _functions(self, tier: int, hint: int = 100) -> list[_Cand]:
        cands = []
        call = self.after != "("
        for spec in self.dialect.functions:
            name = self._case(spec.name)
            cands.append(
                _Cand(
                    name,
                    name + ("()" if call else ""),
                    Kind.FUNCTION,
                    tier,
                    hint=hint,
                    detail=spec.signature,
                    doc=partial(_function_doc, spec),
                    usage=f"function:{spec.name}",
                    cursor_back=1 if call else 0,
                )
            )
        return cands

    # ------------------------------------------------------------------ joins

    def _join_conditions(self) -> list[_Cand]:
        ctx = self.ctx
        target = ctx.join_target
        if target is None or target.table is None:
            return []
        cands: list[_Cand] = []
        for other in ctx.sources:
            if other is target or other.table is None:
                continue
            for fk, holder in SchemaIndex.foreign_keys_between(target.table, other.table):
                sides = [(target, other)] if holder is target.table else [(other, target)]
                if target.table is other.table:  # a self join: either side may hold the key
                    sides = [(target, other), (other, target)]
                for owner, referenced in sides:
                    pairs = [
                        f"{self._ident(owner.ref)}.{self._ident(c)} = "
                        f"{self._ident(referenced.ref)}.{self._ident(rc)}"
                        for c, rc in zip(fk.columns, fk.ref_columns, strict=False)
                    ]
                    text = " AND ".join(pairs)
                    cands.append(
                        _Cand(
                            text,
                            text,
                            Kind.JOIN,
                            -1,
                            detail=self._fk_detail(fk, holder),
                            doc=_fk_text(fk, holder),
                            usage=f"join:{target.table.key}:{other.table.key}:{text}",
                        )
                    )
        return cands

    @staticmethod
    def _fk_detail(fk: ForeignKey, holder: Table) -> str:
        return t("foreign key {table}({columns})", table=holder.name, columns=", ".join(fk.columns))

    # ------------------------------------------------------------------ qualified names

    def _qualified(self) -> list[_Cand]:
        ctx = self.ctx
        parts = ctx.qualifier
        name = parts[-1].casefold()
        if len(parts) == 1:
            for source in (*ctx.sources, *ctx.outer_sources):
                matches = (source.alias or "").casefold() == name or (
                    source.alias is None and source.name.casefold() == name
                )
                if matches and source.columns:
                    return self._columns([source], tier=0, qualify_always=False)
            for cte in ctx.ctes:
                if cte.name.casefold() == name and cte.columns:
                    return self._columns([cte], tier=0, qualify_always=False)
        table = self.index.table(parts) if self.index else None
        if table is not None:
            source = Source(None, table.name, table, _columns_of(table), "table")
            return self._columns([source], tier=0, qualify_always=False)
        if self.index is not None and len(parts) == 1:
            schema_name = self.index.schema_name(parts[0])
            if schema_name is not None:
                return [
                    _Cand(
                        other.name,
                        self._ident(other.name),
                        Kind.VIEW if other.is_view else Kind.TABLE,
                        0,
                        detail=t("{n} cols", n=len(other.columns)),
                        doc=_table_doc(other),
                        usage=f"table:{other.key}",
                    )
                    for other in self.index.tables_of(schema_name)
                ]
        return []

    # ------------------------------------------------------------------ column lists, types

    def _target_columns(self) -> list[_Cand]:
        ctx = self.ctx
        sources: list[Source]
        if ctx.target is not None and ctx.target.columns:
            sources = [ctx.target]
        else:  # USING (...): columns the joined tables share
            counts: dict[str, int] = {}
            for source in ctx.sources:
                for column in source.columns:
                    counts[column.name.casefold()] = counts.get(column.name.casefold(), 0) + 1
            shared = ctx.sources[:1]
            sources = [
                Source(None, s.name, s.table,
                       tuple(c for c in s.columns if counts[c.name.casefold()] > 1), s.kind)
                for s in shared
            ]  # fmt: skip
        listed = {n.casefold() for n in ctx.listed}
        cands = [
            c
            for c in self._columns(sources, tier=0, qualify_always=False)
            if c.label.casefold() not in listed
        ]
        for position, candidate in enumerate(cands):
            candidate.hint = position  # a column list reads best in the table's own order
        return cands

    def _types(self) -> list[_Cand]:
        return [
            _Cand(
                self._case(name),
                self._case(name),
                Kind.TYPE,
                0,
                detail=t("type"),
                usage=f"type:{name}",
            )
            for name in self.dialect.data_types
        ]

    # ------------------------------------------------------------------ after an expression

    def _after_operand(self) -> list[_Cand]:
        ctx = self.ctx
        cands: list[_Cand] = []
        words = self._continuations()
        cands.extend(self._keyword(w, 0, i) for i, w in enumerate(words))
        if ctx.clause in ("FROM", "JOIN") and ctx.sources:
            cands.extend(self._snippets(Where.TABLE_DONE, tier=1))
        elif ctx.clause and ctx.first_word not in ("CREATE", "ALTER", "DROP"):
            cands.extend(self._snippets(Where.CLAUSE, tier=1))
        if ctx.star is not None and self.forced:
            star = self._star_expansion()
            if star is not None:
                cands.append(star)
        return cands

    def _continuations(self) -> tuple[str, ...]:
        ctx = self.ctx
        first = ctx.first_word
        last = ctx.prev_word
        if first == "CREATE":
            if last in ("CREATE", "TEMP", "TEMPORARY", "UNIQUE", "MATERIALIZED", "OR", "REPLACE"):
                return kw.allowed(kw.CREATE_OBJECTS, self.dialect)
            if "TABLE" in ctx.words:
                return kw.COLUMN_CONSTRAINTS
            if "INDEX" in ctx.words and "ON" not in ctx.words:
                return ("ON",)
            return ()
        if first == "DROP":
            return kw.allowed(kw.DROP_OBJECTS, self.dialect) if len(ctx.words) <= 1 else ()
        if first == "ALTER":
            return kw.ALTER_ACTIONS if "TABLE" in ctx.words else ("TABLE", "INDEX", "VIEW")
        if last in kw.FOLLOWERS:
            return kw.allowed(kw.FOLLOWERS[last], self.dialect)
        return kw.allowed(kw.NEXT_BY_CLAUSE.get(ctx.clause, ()), self.dialect)

    def _star_expansion(self) -> _Cand | None:
        ctx = self.ctx
        if ctx.star is None:
            return None
        start, end, alias = ctx.star
        sources = ctx.sources
        if alias is not None:
            sources = [
                s for s in sources
                if (s.alias or s.name).casefold() == alias.casefold()
            ]  # fmt: skip
        sources = [s for s in sources if s.columns]
        if not sources:
            return None
        qualify = len(ctx.sources) > 1 or alias is not None
        names = [
            f"{self._ident(s.ref)}.{self._ident(c.name)}"
            if qualify and s.ref
            else self._ident(c.name)
            for s in sources
            for c in s.columns
        ]
        text = ", ".join(names)
        return _Cand(
            "* → " + ", ".join(names[:3]) + (" …" if len(names) > 3 else ""),
            text,
            Kind.STAR,
            -1,
            detail=t("{n} columns", n=len(names)),
            doc=t("Replace * with the list of columns"),
            replace=(start, end),
            usage="star:expand",
        )


# ---------------------------------------------------------------------- documentation text


def _function_doc(spec: FunctionSpec) -> str:
    kinds = {
        FunctionCategory.AGGREGATE: t("aggregate function"),
        FunctionCategory.WINDOW: t("window function"),
        FunctionCategory.STRING: t("string function"),
        FunctionCategory.NUMERIC: t("numeric function"),
        FunctionCategory.DATETIME: t("date and time function"),
        FunctionCategory.CONDITIONAL: t("conditional expression"),
        FunctionCategory.JSON: t("JSON function"),
        FunctionCategory.ARRAY: t("array function"),
        FunctionCategory.SYSTEM: t("system function"),
        FunctionCategory.OTHER: t("function"),
    }
    return f"{spec.signature}\n{kinds[spec.category]}"


def _columns_of(table: Table) -> tuple[SourceColumn, ...]:
    foreign = table.foreign_key_columns
    return tuple(
        SourceColumn(c.name, c.type, c.primary_key, c.name in foreign, c.nullable, c.comment)
        for c in table.columns
    )


def _column_key(source: Source, column: SourceColumn) -> str:
    if source.table is not None:
        return f"column:{source.table.key}.{column.name}"
    return f"column:{source.ref}.{column.name}"


def _column_doc(source: Source, column: SourceColumn) -> Callable[[], str]:
    def doc() -> str:
        lines = [f"{column.name}  {column.type}".strip()]
        flags = []
        if column.primary_key:
            flags.append(t("primary key"))
        if not column.nullable:
            flags.append(t("NOT NULL"))
        if flags:
            lines.append(" · ".join(flags))
        if source.table is not None:
            for fk in source.table.foreign_keys:
                if column.name in fk.columns:
                    position = fk.columns.index(column.name)
                    target = fk.ref_columns[position] if position < len(fk.ref_columns) else "?"
                    lines.append(f"→ {fk.ref_table}.{target}")
            lines.append(t("in {name}", name=source.table.name))
        elif source.name:
            lines.append(t("in {name}", name=source.name))
        if column.comment:
            lines.append("")
            lines.append(column.comment)
        return "\n".join(lines)

    return doc


def _table_doc(table: Table) -> Callable[[], str]:
    def doc() -> str:
        lines = []
        if table.comment:
            lines.append(table.comment)
            lines.append("")
        names = [c.name for c in table.columns[:8]]
        more = len(table.columns) - len(names)
        lines.append(
            t("{n} columns: {names}", n=len(table.columns), names=", ".join(names))
            + (" …" if more > 0 else "")
        )
        if table.row_estimate:
            lines.append(t("about {n} rows", n=f"{table.row_estimate:,}"))
        return "\n".join(lines)

    return doc


def _fk_text(fk: ForeignKey, holder: Table) -> str:
    return t(
        "foreign key {table}({columns}) → {ref}({ref_columns})",
        table=holder.name,
        columns=", ".join(fk.columns),
        ref=fk.ref_table,
        ref_columns=", ".join(fk.ref_columns),
    )

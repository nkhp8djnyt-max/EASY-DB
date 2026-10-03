from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from ipaddress import IPv4Address
from uuid import UUID

import pytest

from easydbms.core.dialects import TypeKind
from easydbms.core.editing import ValueParseError, edit_text, is_editable, parse_value, same_value

K = TypeKind


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        (K.INTEGER, " 42 ", 42),
        (K.INTEGER, "-7", -7),
        (K.DECIMAL, "12.50", Decimal("12.50")),
        (K.DECIMAL, "1e3", Decimal("1E+3")),
        (K.FLOAT, "2.5", 2.5),
        (K.BOOLEAN, "TRUE", True),
        (K.BOOLEAN, "no", False),
        (K.BOOLEAN, "1", True),
        (K.DATE, "2024-02-29", date(2024, 2, 29)),
        (K.TIME, "13:45:10", time(13, 45, 10)),
        (K.DATETIME, "2024-02-29 13:45:10", datetime(2024, 2, 29, 13, 45, 10)),
        (K.DATETIME, "2024-02-29T13:45:10.5", datetime(2024, 2, 29, 13, 45, 10, 500000)),
        (
            K.DATETIME,
            "2024-02-29 13:45:10Z",
            datetime(2024, 2, 29, 13, 45, 10, tzinfo=UTC),
        ),
        (K.DATETIME, "2024-02-29", datetime(2024, 2, 29)),
        (K.JSON, ' {"a": 1} ', '{"a": 1}'),
        (K.UUID, "12345678-1234-5678-1234-567812345678", "12345678-1234-5678-1234-567812345678"),
        (K.UUID, "12345678123456781234567812345678", "12345678-1234-5678-1234-567812345678"),
        (K.TEXT, "  keep  spaces ", "  keep  spaces "),
        (K.TEXT, "", ""),
        (K.ENUM, "red", "red"),
        (K.OTHER, "10.0.0.1", "10.0.0.1"),
    ],
)
def test_parse_value(kind: TypeKind, text: str, expected: object) -> None:
    value = parse_value(kind, text)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    "kind",
    [
        K.INTEGER,
        K.DECIMAL,
        K.FLOAT,
        K.BOOLEAN,
        K.DATE,
        K.TIME,
        K.DATETIME,
        K.JSON,
        K.UUID,
        K.ENUM,
        K.OTHER,
    ],
)
def test_empty_input_means_null_except_for_text(kind: TypeKind) -> None:
    assert parse_value(kind, "") is None
    assert parse_value(kind, "   ") is None
    assert parse_value(TypeKind.TEXT, "   ") == "   "


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (K.INTEGER, "4.5"),
        (K.INTEGER, "abc"),
        (K.DECIMAL, "1,5"),
        (K.DECIMAL, "NaN"),
        (K.DECIMAL, "Infinity"),
        (K.FLOAT, "nan"),
        (K.FLOAT, "inf"),
        (K.FLOAT, "x"),
        (K.BOOLEAN, "maybe"),
        (K.DATE, "2024-13-01"),
        (K.DATE, "29.02.2024"),
        (K.TIME, "25:00"),
        (K.DATETIME, "yesterday"),
        (K.JSON, "{broken"),
        (K.UUID, "not-a-uuid"),
    ],
)
def test_invalid_text_is_reported_with_the_expected_kind(kind: TypeKind, text: str) -> None:
    with pytest.raises(ValueParseError) as caught:
        parse_value(kind, text)
    assert caught.value.kind is kind
    assert caught.value.text == text


def test_binary_and_array_columns_are_not_editable() -> None:
    assert not is_editable(K.BINARY)
    assert not is_editable(K.ARRAY)
    assert all(is_editable(k) for k in TypeKind if k not in (K.BINARY, K.ARRAY))


@pytest.mark.parametrize(
    ("kind", "value", "text"),
    [
        (K.TEXT, None, ""),
        (K.BOOLEAN, True, "true"),
        (K.BOOLEAN, False, "false"),
        (K.INTEGER, 5, "5"),
        (K.DECIMAL, Decimal("1E+3"), "1000"),
        (K.DECIMAL, Decimal("12.50"), "12.50"),
        (K.DATE, date(2024, 2, 29), "2024-02-29"),
        (K.TIME, time(1, 2, 3), "01:02:03"),
        (K.DATETIME, datetime(2024, 2, 29, 13, 45, 10), "2024-02-29 13:45:10"),
        (K.TIME, timedelta(hours=1, minutes=2, seconds=3), "01:02:03"),
        (K.TIME, timedelta(hours=1, microseconds=5), "01:00:00.000005"),
        (K.TIME, timedelta(hours=-1), "-01:00:00"),
        (K.UUID, UUID(int=1), "00000000-0000-0000-0000-000000000001"),
    ],
)
def test_edit_text(kind: TypeKind, value: object, text: str) -> None:
    assert edit_text(kind, value) == text


@pytest.mark.parametrize(
    "kind", [K.INTEGER, K.DECIMAL, K.BOOLEAN, K.DATE, K.TIME, K.DATETIME, K.UUID, K.TEXT]
)
def test_what_the_editor_shows_parses_back_to_the_same_value(kind: TypeKind) -> None:
    samples = {
        K.INTEGER: 12,
        K.DECIMAL: Decimal("3.140"),
        K.BOOLEAN: True,
        K.DATE: date(2020, 1, 31),
        K.TIME: time(23, 59, 1),
        K.DATETIME: datetime(2020, 1, 31, 23, 59, 1, 250000),
        K.UUID: UUID(int=99),
        K.TEXT: "hello",
    }
    original = samples[kind]
    again = parse_value(kind, edit_text(kind, original))
    assert same_value(kind, original, again)


@pytest.mark.parametrize(
    ("kind", "left", "right", "same"),
    [
        (K.DECIMAL, Decimal("5.0"), Decimal("5.00"), True),
        (K.DECIMAL, Decimal("5"), 5, True),
        (K.INTEGER, 5, Decimal("5"), True),
        (K.INTEGER, 5, 6, False),
        (K.BOOLEAN, 1, True, True),
        (K.BOOLEAN, 0, True, False),
        (K.TIME, timedelta(hours=1), time(1, 0), True),
        (K.TIME, timedelta(hours=25), time(1, 0), False),
        (K.UUID, UUID(int=1), "00000000-0000-0000-0000-000000000001", True),
        (K.TEXT, "a", "A", False),
        (K.TEXT, "", None, False),
        (K.TEXT, None, None, True),
        (K.INTEGER, None, 0, False),
        (K.DATETIME, datetime(2024, 1, 1), datetime(2024, 1, 1, tzinfo=UTC), False),
        (K.DATETIME, datetime(2024, 1, 1), datetime(2024, 1, 1), True),
        (K.BINARY, b"ab", bytearray(b"ab"), True),
    ],
)
def test_same_value(kind: TypeKind, left: object, right: object, same: bool) -> None:
    assert same_value(kind, left, right) is same
    assert same_value(kind, right, left) is same


@pytest.mark.parametrize(
    ("kind", "left", "right", "same"),
    [
        (K.JSON, {"b": 1, "a": [1, 2]}, '{"a": [1, 2], "b": 1}', True),
        (K.JSON, {"a": 1}, '{"a":1}', True),
        (K.JSON, {"a": 1}, '{"a": 2}', False),
        (K.JSON, '{"a": 1}', '{ "a" : 1 }', True),
        (K.JSON, [1, 2], "[1,2]", True),
        (K.JSON, "not json", "not json", True),
        (K.OTHER, IPv4Address("10.0.0.1"), "10.0.0.1", True),
        (K.OTHER, IPv4Address("10.0.0.1"), "10.0.0.2", False),
        (K.OTHER, timedelta(hours=1), "01:00:00", True),
    ],
)
def test_structured_values_are_compared_by_content(
    kind: TypeKind, left: object, right: object, same: bool
) -> None:
    assert same_value(kind, left, right) is same
    assert same_value(kind, right, left) is same


def test_a_parsed_json_document_is_shown_as_json_text() -> None:
    assert edit_text(K.JSON, {"a": [1, "é"]}) == '{"a": [1, "é"]}'
    assert edit_text(K.JSON, [1, 2]) == "[1, 2]"
    assert parse_value(K.JSON, edit_text(K.JSON, {"a": 1})) == '{"a": 1}'


@pytest.mark.parametrize(
    ("kind", "left", "right", "same"),
    [
        (K.DATETIME, "2024-02-29 13:45:10", datetime(2024, 2, 29, 13, 45, 10), True),
        (K.DATETIME, "2024-02-29T13:45:10", datetime(2024, 2, 29, 13, 45, 10), True),
        (K.DATETIME, "2024-02-29 13:45:11", datetime(2024, 2, 29, 13, 45, 10), False),
        (K.DATETIME, "yesterday", datetime(2024, 2, 29), False),
        (K.DATE, "2024-02-29", date(2024, 2, 29), True),
        (K.TIME, "13:45:10", time(13, 45, 10), True),
        (K.DECIMAL, 0.1, Decimal("0.1"), True),
        (K.DECIMAL, 1234.5, Decimal("1234.50"), True),
        (K.DECIMAL, 0.1, Decimal("0.2"), False),
        (K.INTEGER, 5.0, 5, True),
        (K.FLOAT, 0.1, 0.1, True),
        (K.FLOAT, 0.1, 0.1000000001, False),
        (K.DECIMAL, "12", Decimal("12"), False),
    ],
)
def test_text_and_float_stored_values_match_what_is_typed(
    kind: TypeKind, left: object, right: object, same: bool
) -> None:
    assert same_value(kind, left, right) is same
    assert same_value(kind, right, left) is same

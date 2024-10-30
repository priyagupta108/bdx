import pytest
import xapian
from bdx.index import DatabaseField, IntegerField, PathField, Schema
from bdx.query_parser import QueryParser
from pytest import fixture

AND = 0
OR = 1
WILDCARD = 2
VALUE_RANGE = 3
VALUE_GE = 4
VALUE_LE = 5
AND_NOT = 6
MATCH_ALL = xapian.Query.MatchAll  # pyright: ignore


@fixture
def query_parser(monkeypatch):
    schema = Schema([DatabaseField("name", "XNAME")])
    parser = QueryParser(schema)
    monkeypatch.setattr(xapian, "Query", lambda *args: tuple(args))
    monkeypatch.setattr(xapian.Query, "OP_AND", AND, raising=False)
    monkeypatch.setattr(xapian.Query, "OP_OR", OR, raising=False)
    monkeypatch.setattr(xapian.Query, "OP_WILDCARD", WILDCARD, raising=False)
    monkeypatch.setattr(
        xapian.Query, "OP_VALUE_RANGE", VALUE_RANGE, raising=False
    )
    monkeypatch.setattr(xapian.Query, "OP_VALUE_GE", VALUE_GE, raising=False)
    monkeypatch.setattr(xapian.Query, "OP_VALUE_LE", VALUE_LE, raising=False)
    monkeypatch.setattr(xapian.Query, "OP_AND_NOT", AND_NOT, raising=False)
    monkeypatch.setattr(
        xapian.Query, "WILDCARD_LIMIT_FIRST", 10, raising=False
    )

    yield parser


def test_empty(query_parser):
    assert query_parser.parse_query("") == ()
    assert query_parser.parse_query("  ") == ()
    assert query_parser.parse_query("  \n   ") == ()


def test_matchall(query_parser):
    assert query_parser.parse_query("  *:*  ") == MATCH_ALL


def test_not(query_parser):
    assert query_parser.parse_query("NOT foo") == (
        AND_NOT,
        MATCH_ALL,
        ("XNAMEfoo",),
    )
    assert query_parser.parse_query("!foo") == (
        AND_NOT,
        MATCH_ALL,
        ("XNAMEfoo",),
    )
    assert query_parser.parse_query("NOT foo bar") == (
        AND,
        (AND_NOT, MATCH_ALL, ("XNAMEfoo",)),
        ("XNAMEbar",),
    )
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("NOT")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("!NOT")


def test_invalid_token(query_parser):
    query_parser.ignore_unknown_tokens = False
    with pytest.raises(QueryParser.UnknownTokenError):
        query_parser.parse_query(":")
    with pytest.raises(QueryParser.UnknownTokenError):
        query_parser.parse_query("#")
    with pytest.raises(QueryParser.UnknownTokenError):
        query_parser.parse_query("%")
    with pytest.raises(QueryParser.UnknownTokenError):
        query_parser.parse_query("foo :")


def test_single_term(query_parser):
    assert query_parser.parse_query("foo") == ("XNAMEfoo",)
    assert query_parser.parse_query("  foo  ") == ("XNAMEfoo",)


def test_multiple_terms(query_parser):
    assert query_parser.parse_query("foo bar") == (
        AND,
        ("XNAMEfoo",),
        ("XNAMEbar",),
    )
    assert query_parser.parse_query("foo bar baz") == (
        AND,
        ("XNAMEfoo",),
        (
            AND,
            ("XNAMEbar",),
            ("XNAMEbaz",),
        ),
    )


def test_string(query_parser):
    assert query_parser.parse_query(' "foo baz"') == ("XNAMEfoo baz",)


def test_field_with_value(query_parser):
    assert query_parser.parse_query("name:bar") == ("XNAMEbar",)
    assert query_parser.parse_query("name: FOO") == ("XNAMEfoo",)


def test_field_with_string_value(query_parser):
    assert query_parser.parse_query('name:"foo bar"') == ("XNAMEfoo bar",)


def test_wildcard(query_parser):
    query_parser.wildcard_field = "name"
    assert query_parser.parse_query("fo*") == (
        WILDCARD,
        "XNAMEfo",
        0,
        xapian.Query.WILDCARD_LIMIT_FIRST,
    )
    assert query_parser.parse_query("name:fo*") == (
        WILDCARD,
        "XNAMEfo",
        0,
        xapian.Query.WILDCARD_LIMIT_FIRST,
    )
    assert query_parser.parse_query("name:foo.b*") == (
        WILDCARD,
        "XNAMEfoo.b",
        0,
        xapian.Query.WILDCARD_LIMIT_FIRST,
    )


def test_wildcard_with_no_wildcard_field(query_parser):
    query_parser.wildcard_field = None
    assert query_parser.parse_query("fo*") == ()
    assert query_parser.parse_query("name:fo*") == (
        WILDCARD,
        "XNAMEfo",
        0,
        xapian.Query.WILDCARD_LIMIT_FIRST,
    )


def test_auto_wildcard(query_parser):
    query_parser.wildcard_field = "name"
    query_parser.auto_wildcard = True
    assert query_parser.parse_query("fo") == (
        WILDCARD,
        "XNAMEfo",
        0,
        xapian.Query.WILDCARD_LIMIT_FIRST,
    )
    assert query_parser.parse_query("name:fo") == ("XNAMEfo",)


def test_intrange(query_parser):
    slot = 99928
    query_parser.schema = schema = Schema(
        [
            IntegerField("value", "XV", slot=slot),
        ]
    )
    query_parser.default_fields = ["value"]

    assert query_parser.parse_query("123..456") == (
        VALUE_RANGE,
        slot,
        schema["value"].preprocess_value(123),
        schema["value"].preprocess_value(456),
    )
    assert query_parser.parse_query("..987") == (
        VALUE_LE,
        slot,
        schema["value"].preprocess_value(987),
    )
    assert query_parser.parse_query("369..") == (
        VALUE_GE,
        slot,
        schema["value"].preprocess_value(369),
    )
    assert query_parser.parse_query("369") == (
        VALUE_RANGE,
        slot,
        schema["value"].preprocess_value(369),
        schema["value"].preprocess_value(369),
    )

    query_parser.schema = schema = Schema(
        [
            IntegerField("value", "XV", slot=slot),
            IntegerField("other_value", "XV2", slot=slot + 1),
        ]
    )

    assert query_parser.parse_query("value:..12346") == (
        VALUE_LE,
        slot,
        schema["value"].preprocess_value(12346),
    )
    assert query_parser.parse_query("value:99182") == (
        VALUE_RANGE,
        slot,
        schema["value"].preprocess_value(99182),
        schema["value"].preprocess_value(99182),
    )
    assert query_parser.parse_query("val:..12346") == ()

    assert query_parser.parse_query("value:..12346 AND other_value:10..") == (
        AND,
        (
            VALUE_LE,
            slot,
            schema["value"].preprocess_value(12346),
        ),
        (
            VALUE_GE,
            slot + 1,
            schema["other_value"].preprocess_value(10),
        ),
    )


def test_path_field(query_parser):
    query_parser.schema = Schema(
        [
            DatabaseField("name", "XNAME"),
            PathField("path", "XPATH"),
        ]
    )

    assert query_parser.parse_query("name:BAR") == ("XNAMEbar",)
    assert query_parser.parse_query("path:FOO") == ("XPATHFOO",)

    query_parser.default_fields = ["name", "path"]
    assert query_parser.parse_query("FOO") == (
        OR,
        [("XNAMEfoo",), ("XPATHFOO",)],
    )


def test_single_term_no_default_fields(query_parser):
    query_parser.default_fields = []
    assert query_parser.parse_query("foo") == (OR, [])
    assert query_parser.parse_query('"foo bar"') == (OR, [])
    assert query_parser.parse_query("name:foo") == ("XNAMEfoo",)


def test_field_with_no_value(query_parser):
    query_parser.ignore_missing_field_values = False
    with pytest.raises(QueryParser.Error, match=r"\bfoo\b.*at position 4"):
        query_parser.parse_query("foo:")

    query_parser.schema = Schema(
        [DatabaseField("name", "XNAME"), DatabaseField("path", "XPATH")]
    )
    query_parser.ignore_missing_field_values = True
    assert query_parser.parse_query("name: path:baz") == (
        AND,
        (),
        ("XPATHbaz",),
    )
    assert query_parser.parse_query("name: OR path:baz") == ("XPATHbaz",)


def test_unknown_field(query_parser):
    assert query_parser.parse_query("unknown:text") == ()
    assert query_parser.parse_query("name:foo unknown:text name:bar") == (
        AND,
        ("XNAMEfoo",),
        (
            AND,
            # Matches nothing, so the whole query will match nothing.
            (),
            ("XNAMEbar",),
        ),
    )


def test_multiple_default_fields(query_parser):
    query_parser.schema = Schema(
        [
            DatabaseField("name", "XNAME"),
            DatabaseField("full_name", "XFULLNAME"),
            DatabaseField("something", "XSOMETHING"),
        ]
    )
    query_parser.default_fields = ["name", "full_name"]
    assert query_parser.parse_query("foo") == (
        OR,
        [
            ("XNAMEfoo",),
            ("XFULLNAMEfoo",),
        ],
    )
    assert query_parser.parse_query('"foo bar"') == (
        OR,
        [
            ("XNAMEfoo bar",),
            ("XFULLNAMEfoo bar",),
        ],
    )


def test_ignores_invalid_tokens(query_parser):
    query_parser.ignore_unknown_tokens = True
    assert query_parser.parse_query("  /~?# foo ?$@#  ") == ("XNAMEfoo",)
    assert query_parser.parse_query("  !/~?# foo ?$@#  ") == (
        AND_NOT,
        MATCH_ALL,
        ("XNAMEfoo",),
    )
    assert query_parser.parse_query("  #name://foo//  ") == ("XNAMEfoo",)
    assert query_parser.parse_query("  #name://foo//bar  ") == (
        AND,
        ("XNAMEfoo",),
        ("XNAMEbar",),
    )
    assert query_parser.parse_query("~@#$%^&*foo+*&^%$#@~") == ("XNAMEfoo",)


def test_or(query_parser):
    assert query_parser.parse_query("foo OR bar") == (
        OR,
        ("XNAMEfoo",),
        ("XNAMEbar",),
    )


def test_and(query_parser):
    assert query_parser.parse_query("foo AND bar") == (
        AND,
        ("XNAMEfoo",),
        ("XNAMEbar",),
    )


def test_operand_missing(query_parser):
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("foo OR")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("OR foo")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("foo OR OR")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("foo OR AND")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("foo AND")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("AND foo")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("foo AND AND")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("foo AND OR")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("NOT")
    with pytest.raises(QueryParser.Error):
        query_parser.parse_query("NOT NOT")


def test_parens(query_parser):
    assert query_parser.parse_query("()") == ()
    assert query_parser.parse_query("(())") == ()
    assert query_parser.parse_query("(foo)") == ("XNAMEfoo",)
    assert query_parser.parse_query("((foo))") == ("XNAMEfoo",)
    assert query_parser.parse_query("((foo) bar)") == (
        AND,
        ("XNAMEfoo",),
        ("XNAMEbar",),
    )
    assert query_parser.parse_query("foo ()") == ("XNAMEfoo",)
    assert query_parser.parse_query("foo () bar") == (
        AND,
        ("XNAMEfoo",),
        ("XNAMEbar",),
    )

    assert query_parser.parse_query("foo AND bar OR baz") == (
        OR,
        (
            AND,
            ("XNAMEfoo",),
            ("XNAMEbar",),
        ),
        ("XNAMEbaz",),
    )

    assert query_parser.parse_query("foo AND (bar OR baz)") == (
        AND,
        ("XNAMEfoo",),
        (
            OR,
            ("XNAMEbar",),
            ("XNAMEbaz",),
        ),
    )


def test_missing_closing_paren(query_parser):
    with pytest.raises(
        QueryParser.Error, match=r'closing "[)]".*at position 1.*at position 5'
    ):
        assert query_parser.parse_query(" (foo")

from bdx.index import SymbolNameField


def test_tokenize_symbol():
    tokens = SymbolNameField.tokenize_value("foo")
    assert tokens == set(
        [
            "foo",
        ]
    )

    tokens = SymbolNameField.tokenize_value("foo_bar")
    assert tokens == set(
        [
            "bar",
            "foo",
        ]
    )

    tokens = SymbolNameField.tokenize_value("_foo123_bar37_")
    assert tokens == set(
        [
            "foo",
            "foo123",
            "123",
            "bar",
            "37",
            "bar37",
        ]
    )

    tokens = SymbolNameField.tokenize_value("__foo_bar__")
    assert tokens == set(
        [
            "bar",
            "foo",
        ]
    )

    tokens = SymbolNameField.tokenize_value("FooBarCamelCase")
    assert tokens == set(
        [
            "Bar",
            "Camel",
            "Case",
            "Foo",
            "FooBarCamelCase",
        ]
    )

    tokens = SymbolNameField.tokenize_value("LSDigitVALUE")
    assert tokens == set(
        [
            "Digit",
            "LSD",
            "LSDigitVALUE",
            "VALUE",
        ]
    )

    tokens = SymbolNameField.tokenize_value(
        "_Z37cxxFunctionReturningStdVectorOfStringB5cxx11v"
    )
    assert tokens == set(
        [
            "11",
            "37",
            "5",
            "Function",
            "Of",
            "Returning",
            "Std",
            "String",
            "Vector",
            "Z37",
            "cxx",
            "cxx11",
            "cxxFunctionReturningStdVectorOfStringB",
            "cxxFunctionReturningStdVectorOfStringB5",
        ]
    )

    tokens = SymbolNameField.tokenize_value(
        "_Z39cxxFunctionAcceptingBoostVectorOfStringN5boost9container6vectorINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEvvEE"
    )
    assert tokens == set(
        [
            "11",
            "1112",
            "39",
            "5",
            "6",
            "7",
            "9",
            "Accepting",
            "Boost",
            "EE",
            "EEE",
            "ES",
            "Evv",
            "Function",
            "INS",
            "Ic",
            "Of",
            "Sa",
            "St",
            "String",
            "Vector",
            "Z39",
            "basic",
            "boost",
            "boost9",
            "char",
            "container",
            "container6",
            "cxx",
            "cxx1112",
            "cxxFunctionAcceptingBoostVectorOfStringN",
            "cxxFunctionAcceptingBoostVectorOfStringN5",
            "stringIcSt",
            "stringIcSt11",
            "traitsIcESaIcEEEvvEE",
            "vectorINSt",
            "vectorINSt7",
        ]
    )

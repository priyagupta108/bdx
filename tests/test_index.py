import os
from contextlib import contextmanager
from pathlib import Path
from shutil import rmtree

import pytest

# isort: off
from bdx.index import (
    IndexingOptions,
    SymbolIndex,
    SymbolNameField,
    index_binary_directory,
)

# isort: on

FIXTURE_PATH = Path(__file__).parent / "fixture"


@contextmanager
def chdir(dir):
    old = os.getcwd()
    try:
        os.chdir(dir)
        yield
    finally:
        os.chdir(old)


@pytest.fixture(scope="module")
def readonly_index(tmp_path_factory):
    index_path = tmp_path_factory.mktemp("index")
    index_binary_directory(FIXTURE_PATH, index_path, IndexingOptions())
    with SymbolIndex.open(index_path, readonly=True) as index:
        yield index


def test_indexing(tmp_path):
    index_path = tmp_path / "index"
    index_binary_directory(FIXTURE_PATH, index_path, IndexingOptions())

    with SymbolIndex.open(index_path, readonly=True) as index:
        symbols = index.search("*:*")
        assert symbols.count == 13
        by_name = {x.name: x for x in symbols}

        top_level_symbol = by_name["top_level_symbol"]
        other_top_level_symbol = by_name["other_top_level_symbol"]
        bar = by_name["bar"]
        cxx_function = by_name["_Z12cxx_functionSt6vectorIiSaIiEE"]
        foo = by_name["foo"]
        c_function = by_name["c_function"]
        camel_case_symbol = by_name["CamelCaseSymbol"]
        cpp_camel_case_symbol = by_name["_Z18CppCamelCaseSymbolPKc"]

        assert top_level_symbol.path == FIXTURE_PATH / "toplev.c.o"
        assert top_level_symbol.name == "top_level_symbol"
        assert top_level_symbol.section == ".rodata"
        assert top_level_symbol.address == 0
        assert top_level_symbol.size == 64
        assert top_level_symbol.relocations == []
        assert top_level_symbol.mtime > 0

        assert other_top_level_symbol.path == FIXTURE_PATH / "toplev.c.o"
        assert other_top_level_symbol.name == "other_top_level_symbol"
        assert other_top_level_symbol.section == ".data.rel.ro.local"
        assert other_top_level_symbol.address == 0
        assert other_top_level_symbol.size == 8
        assert other_top_level_symbol.relocations == ["top_level_symbol"]
        assert other_top_level_symbol.mtime > 0

        assert bar.path == FIXTURE_PATH / "subdir" / "bar.cpp.o"
        assert bar.name == "bar"
        assert bar.section == ".bss"
        assert bar.relocations == []

        assert cxx_function.path == FIXTURE_PATH / "subdir" / "bar.cpp.o"
        assert cxx_function.name == "_Z12cxx_functionSt6vectorIiSaIiEE"
        assert cxx_function.section == ".text"
        assert cxx_function.relocations == [
            "bar",
            "foo",
        ]

        assert foo.path == FIXTURE_PATH / "subdir" / "foo.c.o"
        assert foo.name == "foo"
        assert foo.section == ".bss"
        assert foo.relocations == []

        assert c_function.path == FIXTURE_PATH / "subdir" / "foo.c.o"
        assert c_function.name == "c_function"
        assert c_function.section == ".text"
        assert c_function.relocations == [
            "foo",
        ]

        for i in range(5):
            symbol = by_name[f"a_name{i}"]
            assert symbol.path == FIXTURE_PATH / "subdir" / "foo.c.o"
            assert symbol.name == f"a_name{i}"
            assert symbol.section == ".bss"
            assert symbol.relocations == []

        assert camel_case_symbol.path == FIXTURE_PATH / "subdir" / "foo.c.o"
        assert camel_case_symbol.name == "CamelCaseSymbol"
        assert camel_case_symbol.section == ".text"
        assert camel_case_symbol.relocations == []

        assert (
            cpp_camel_case_symbol.path == FIXTURE_PATH / "subdir" / "bar.cpp.o"
        )
        assert cpp_camel_case_symbol.name == "_Z18CppCamelCaseSymbolPKc"
        assert cpp_camel_case_symbol.section == ".text"
        assert cpp_camel_case_symbol.relocations == []


def test_indexing_min_symbol_size(tmp_path):
    index_path = tmp_path / "index"
    for msize in [0, 1, 64, 65]:
        try:
            rmtree(index_path)
        except FileNotFoundError:
            pass

        index_binary_directory(
            FIXTURE_PATH, index_path, IndexingOptions(min_symbol_size=msize)
        )

        with SymbolIndex.open(index_path, readonly=True) as index:
            symbols = set(index.search("*:*"))
            by_name = {x.name: x for x in symbols}
            assert symbols

            for sym in symbols:
                # One entry (with an empty name) per file is when
                # no regular symbols were added
                if sym.name:
                    assert sym.size >= msize

            if msize <= 64:
                assert "top_level_symbol" in by_name
            else:
                assert "top_level_symbol" not in by_name


def test_indexing_without_relocations(tmp_path):
    index_path = tmp_path / "index"
    index_binary_directory(
        FIXTURE_PATH, index_path, IndexingOptions(index_relocations=False)
    )

    with SymbolIndex.open(index_path, readonly=True) as index:
        symbols = list(index.search("*:*"))
        assert symbols

        for symbol in symbols:
            assert not symbol.relocations


def test_searching_by_wildcard(readonly_index):
    symbols = set(readonly_index.search("name:a_*"))
    assert symbols
    for sym in symbols:
        assert sym.name.startswith("a_")

    # Wildcard not provided
    assert not set(readonly_index.search("name:a_"))

    # Automatically search by name
    assert set(readonly_index.search("a_*")) == symbols

    # Automatically append a wildcard if a field is not specified
    assert set(readonly_index.search("a_")) == symbols


def test_searching_camel_case(readonly_index):
    symbols = set(readonly_index.search("camel"))
    assert symbols
    by_name = {x.name: x for x in symbols}

    assert "CamelCaseSymbol" in by_name
    ccs = by_name["CamelCaseSymbol"]
    assert ccs in readonly_index.search("case")
    assert ccs in readonly_index.search("cam ca sym")
    assert ccs in readonly_index.search("cam ca")
    assert ccs in readonly_index.search("cas sym")
    assert ccs in readonly_index.search("symbol")
    assert ccs in readonly_index.search("camelc*")
    assert ccs in readonly_index.search("Camel")
    assert ccs in readonly_index.search("CamelC*")
    assert ccs in readonly_index.search("CamelCase")
    assert ccs in readonly_index.search("camelcaseS*")

    assert "_Z18CppCamelCaseSymbolPKc" in by_name
    ccs = by_name["_Z18CppCamelCaseSymbolPKc"]
    assert ccs in readonly_index.search("case")
    assert ccs in readonly_index.search("cam ca sym")
    assert ccs in readonly_index.search("cam ca")
    assert ccs in readonly_index.search("cas sym")
    assert ccs in readonly_index.search("symbol")
    assert ccs in readonly_index.search("cppcamelc*")
    assert ccs in readonly_index.search("Camel")


def test_searching_by_size(readonly_index):
    symbols = readonly_index.search("size:8")
    for sym in symbols:
        assert sym.size == 8
    names = [x.name for x in symbols]
    assert "other_top_level_symbol" in names

    symbols = readonly_index.search("size:32..128")
    for sym in symbols:
        assert 32 <= sym.size <= 128

    names = [x.name for x in symbols]
    assert "top_level_symbol" in names


def test_searching_by_relative_path(readonly_index):
    with chdir(FIXTURE_PATH):
        all_symbols = set(readonly_index.search("*:*"))

        # Ensure the path is normalized
        subdir_symbols = set(readonly_index.search("path:subdir///*"))
        assert subdir_symbols
        for sym in subdir_symbols:
            assert FIXTURE_PATH / "subdir" in sym.path.parents
        for sym in all_symbols.difference(subdir_symbols):
            assert FIXTURE_PATH / "subdir" not in sym.path.parents

    with chdir(FIXTURE_PATH / "subdir"):
        subdir_symbols = set(readonly_index.search("path:./*"))
        assert subdir_symbols
        for sym in subdir_symbols:
            assert FIXTURE_PATH / "subdir" in sym.path.parents
        for sym in all_symbols.difference(subdir_symbols):
            assert FIXTURE_PATH / "subdir" not in sym.path.parents


def test_searching_by_absolute_path(readonly_index):
    with chdir(FIXTURE_PATH):
        all_symbols = set(readonly_index.search("*:*"))
        # Ensure the path is normalized
        foo_symbols = set(
            readonly_index.search(f"path:///{FIXTURE_PATH}///subdir//foo.c.o")
        )
        assert foo_symbols
        for sym in foo_symbols:
            assert sym.path == FIXTURE_PATH / "subdir" / "foo.c.o"
        for sym in all_symbols.difference(foo_symbols):
            assert sym.path != FIXTURE_PATH / "subdir" / "foo.c.o"


def test_searching_by_basename(readonly_index):
    all_symbols = set(readonly_index.search("*:*"))
    bar_symbols = set(readonly_index.search("path:bar.cpp.o"))
    assert bar_symbols
    for sym in bar_symbols:
        assert sym.path == FIXTURE_PATH / "subdir" / "bar.cpp.o"
    for sym in all_symbols.difference(bar_symbols):
        assert sym.path != FIXTURE_PATH / "subdir" / "bar.cpp.o"


def test_searching_cxx(readonly_index):
    symbols = readonly_index.search("cxx func")
    by_name = {x.name: x for x in symbols}

    sym = by_name["_Z12cxx_functionSt6vectorIiSaIiEE"]

    assert sym in readonly_index.search("c fu vec")
    assert sym in readonly_index.search("12 c f v")
    assert sym in readonly_index.search("cxx fu")
    assert sym in readonly_index.search("vector")
    assert sym in readonly_index.search("func vec")


def test_demangling(readonly_index):
    symbols = readonly_index.search("cxx func")
    by_name = {x.name: x for x in symbols}

    sym = by_name["_Z12cxx_functionSt6vectorIiSaIiEE"]
    assert (
        sym.demangle_name()
        == "cxx_function(std::vector<int, std::allocator<int> >)"
    )


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

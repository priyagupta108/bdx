import json
import re

import pytest

# isort: off
from bdx.cli import cli
from click.testing import CliRunner, Result
from pathlib import Path

# isort: on


@pytest.fixture
def index_path(tmp_path):
    return tmp_path / "index"


def index_directory(
    runner: CliRunner, fixture_path: Path, index_path: Path
) -> Result:
    return runner.invoke(
        cli,
        ["index", "--index-path", str(index_path), "-d", str(fixture_path)],
    )


def index_directory_compile_commands(
    runner: CliRunner, index_path: Path
) -> Result:
    return runner.invoke(cli, ["index", "--index-path", str(index_path), "-c"])


def search_directory(runner: CliRunner, index_path: Path, *args) -> Result:
    return runner.invoke(
        cli, ["search", "--index-path", str(index_path), *args]
    )


def test_cli_indexing(fixture_path, index_path):
    runner = CliRunner()
    result = index_directory(runner, fixture_path, index_path)
    assert result.exit_code == 0

    searchresult = search_directory(
        runner, index_path, "--format", "{basename}: {section}: {name}", "*:*"
    )
    assert searchresult.exit_code == 0

    lines = searchresult.output.splitlines()

    assert "foo.c.o: .text: c_function" in lines
    assert "bar.cpp.o: .bss: bar" in lines


def test_cli_indexing_with_compile_commands(fixture_path, index_path, chdir):
    with chdir(fixture_path):
        if not Path("compile_commands.json").exists():
            pytest.skip(
                reason=(
                    "compile_commands.json not generated, do: "
                    f"`make -C {fixture_path} compile_commands.json`"
                )
            )

        runner = CliRunner()

        result = index_directory_compile_commands(runner, index_path)
        assert result.exit_code == 0

        searchresult = search_directory(
            runner, index_path, "-f", "{basename}: {section}: {name}", "*:*"
        )
        assert searchresult.exit_code == 0

        lines = searchresult.output.splitlines()

        assert "foo.c.o: .text: c_function" in lines
        assert "bar.cpp.o: .bss: bar" in lines


def test_cli_search_json_output(fixture_path, index_path):
    runner = CliRunner()
    result = index_directory(runner, fixture_path, index_path)
    assert result.exit_code == 0

    searchresult = search_directory(
        runner,
        index_path,
        "-f",
        "json",
        "--demangle-names",
        "c",
        "funct",
    )
    assert searchresult.exit_code == 0

    results = [json.loads(l) for l in searchresult.output.splitlines()]
    results_by_name = {}
    for x in results:
        del x["mtime"]
        results_by_name[x["name"]] = x

    assert results_by_name["c_function"] == {
        "outdated": False,
        "index": 1,
        "total": 2,
        "path": str(fixture_path / "subdir" / "foo.c.o"),
        "name": "c_function",
        "demangled": "c_function",
        "section": ".text",
        "address": 16,
        "size": 16,
        "type": "FUNC",
        "relocations": ["foo"],
    }
    assert results_by_name["_Z12cxx_functionSt6vectorIiSaIiEE"] == {
        "outdated": False,
        "index": 0,
        "total": 2,
        "path": str(fixture_path / "subdir" / "bar.cpp.o"),
        "name": "_Z12cxx_functionSt6vectorIiSaIiEE",
        "demangled": "cxx_function(std::vector<int, std::allocator<int> >)",
        "section": ".text",
        "address": 0,
        "size": 24,
        "type": "FUNC",
        "relocations": ["bar", "foo"],
    }


def test_cli_search_sexp_output(fixture_path, index_path):
    runner = CliRunner()
    result = index_directory(runner, fixture_path, index_path)
    assert result.exit_code == 0

    searchresult = search_directory(
        runner, index_path, "-f", "sexp", "--demangle-names", "c", "funct"
    )
    assert searchresult.exit_code == 0

    results = searchresult.output.splitlines()
    results = [
        re.sub(f':path "{str(fixture_path)}', ':path "XXX', s) for s in results
    ]
    results = [re.sub(f":mtime [0-9]+", ":mtime XXX", s) for s in results]

    assert (
        "(:outdated nil"
        " :index 0"
        " :total 2"
        ' :path "XXX/subdir/bar.cpp.o"'
        ' :name "_Z12cxx_functionSt6vectorIiSaIiEE"'
        ' :section ".text"'
        " :address 0"
        " :size 24"
        ' :type "FUNC"'
        ' :relocations ("bar" "foo")'
        " :mtime XXX"
        ' :demangled "cxx_function(std::vector<int, std::allocator<int> >)"'
        ")"
    ) in results


def test_cli_file_list(fixture_path, index_path):
    runner = CliRunner()
    result = index_directory(runner, fixture_path, index_path)
    assert result.exit_code == 0

    filesresult = runner.invoke(cli, ["files", "--index-path", index_path])

    assert filesresult.exit_code == 0

    assert set(filesresult.output.splitlines()) == set(
        [
            str(fixture_path / "subdir" / "bar.cpp.o"),
            str(fixture_path / "subdir" / "foo.c.o"),
            str(fixture_path / "toplev.c.o"),
        ]
    )


def test_cli_graph(fixture_path, index_path):
    try:
        import bdx.graph
    except ImportError:
        pytest.skip(reason="Graphs not available, package not installed")

    runner = CliRunner(mix_stderr=False)
    result = index_directory(runner, fixture_path, index_path)
    assert result.exit_code == 0

    graphresult = runner.invoke(
        cli,
        [
            "graph",
            "--index-path",
            index_path,
            "main",
            "c_function",
            "--json-progress",
        ],
    )

    assert graphresult.exit_code == 0
    stderr = graphresult.stderr.splitlines()

    assert "main -- uses_c_function" in graphresult.output
    assert "uses_c_function -- c_function" in graphresult.output

    assert '{"done": 1, "total": 1}' in stderr
    assert '{"found": 1}' in stderr
    assert '{"visited": 2}' in stderr

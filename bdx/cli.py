from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from enum import Enum
from functools import lru_cache, wraps
from pathlib import Path
from sys import exit
from typing import Any, Optional

import click
from click.shell_completion import CompletionItem
from click.types import BoolParamType, IntRange

import bdx
from bdx import debug, error, info, log, make_progress_bar
# fmt: off
from bdx.binary import (BinaryDirectory, NameDemangler, Symbol,
                        find_compilation_database)
from bdx.index import (IndexingOptions, SymbolIndex, index_binary_directory,
                       search_index)
from bdx.query_parser import QueryParser

# fmt: on

try:
    import bdx.graph

    have_graphs = True
except ImportError:
    have_graphs = False


def sexp_format(data: Any) -> str:
    """Format data as a Lisp S-expression.

    Dicts are formatted as plists, with keys formatted in ``:key`` format.
    """
    if isinstance(data, list):
        return "({})".format(" ".join([sexp_format(x) for x in data]))
    elif isinstance(data, dict):

        def fmt(item):
            key, value = item
            return f":{key} {sexp_format(value)}"

        return "({})".format(" ".join([fmt(x) for x in data.items()]))
    elif isinstance(data, (str, int, float)):
        return json.dumps(data)
    msg = f"Invalid value: {data}"
    raise ValueError(msg)


def guess_directory_from_index_path(
    index_path: Optional[Path],
) -> Optional[Path]:
    """Return the path to the binary directory for given index path."""
    if index_path is not None and Path(index_path).exists():
        try:
            with SymbolIndex.open(index_path, readonly=True) as index:
                binary_dir = index.binary_dir()
                if binary_dir is not None:
                    return binary_dir
        except SymbolIndex.Error:
            return None
    return None


def default_directory(ctx: click.Context) -> Path:
    """Return the default binary directory using given CLI context."""
    cwd = Path().absolute()
    if "use_compilation_database" in ctx.params:
        compdb = find_compilation_database(cwd)
        if compdb is not None:
            return compdb.parent

    possible_index_paths = []
    possible_index_paths.append(ctx.params.get("index_path"))
    possible_index_paths.append(SymbolIndex.default_path(Path(".")))
    possible_index_paths.extend(
        [SymbolIndex.default_path(x) for x in cwd.parents]
    )
    for index_path in possible_index_paths:
        directory = guess_directory_from_index_path(index_path)
        if directory:
            return directory

    return cwd


def _common_options(index_must_exist=False):

    def decorator(f):

        @click.option(
            "-d",
            "--directory",
            type=click.Path(
                exists=True,
                dir_okay=True,
                file_okay=False,
                resolve_path=True,
            ),
            help="Path to the binary directory.",
        )
        @click.option(
            "--index-path",
            type=click.Path(
                exists=index_must_exist,
                dir_okay=True,
                file_okay=False,
                resolve_path=True,
            ),
            help="Path to the index.  By default, it is located in ~/.cache.",
        )
        @click.option(
            "-v",
            "--verbose",
            count=True,
            help=(
                "Be verbose.  Can be provided multiple times "
                " for increased verbosity."
            ),
        )
        @click.pass_context
        @wraps(f)
        def inner(
            ctx: click.Context,
            *args,
            directory: str | Path,
            index_path: str | Path,
            verbose: int,
            **kwargs,
        ):
            did_guess_directory = False

            if not directory:
                directory = default_directory(ctx)
                did_guess_directory = True
            if not index_path:
                index_path = SymbolIndex.default_path(directory)

            index_path = Path(index_path)
            directory = Path(directory)

            if index_path.exists():
                try:
                    with SymbolIndex.open(index_path, readonly=True) as index:
                        indexed_dir = index.binary_dir()
                        if (
                            indexed_dir is not None
                            and indexed_dir != directory
                        ):
                            msg = (
                                "Index is for different "
                                f"directory: {indexed_dir}"
                            )
                            raise click.BadParameter(msg)
                except SymbolIndex.Error as e:
                    msg = f"Invalid index: {index_path}"
                    raise click.BadParameter(msg) from e
            elif index_must_exist:
                msg = f"Directory is not indexed: {directory}"
                raise click.UsageError(msg)

            bdx.VERBOSITY = verbose

            if did_guess_directory:
                info(f"note: Using {directory} as binary directory")

            debug("Binary directory: {}", str(directory.absolute()))
            debug("PWD: {}", str(Path().absolute()))

            f(*args, directory, index_path, **kwargs)

        return inner

    return decorator


class IndexingOptionParamType(click.ParamType):
    """Click parameter type for indexing --opt."""

    name = "option"

    OPTIONS = [x for x in dir(IndexingOptions) if not x.startswith("_")]

    CONVERTERS = {
        "num_processes": IntRange(min=1, max=(os.cpu_count() or 1) * 2),
        "index_relocations": BoolParamType(),
        "min_symbol_size": IntRange(min=0),
    }

    def convert(self, value, param, ctx):
        """Convert the given value to correct type, or error out."""
        try:
            k, v = value.split("=", maxsplit=1)
        except ValueError:
            self.fail(f"Argument '{value}' should be of the form 'key=value'")

        if k not in self.OPTIONS:
            self.fail(f"Unknown option '{k}'")

        try:
            return (k, self.CONVERTERS[k].convert(v, param, ctx))
        except click.BadParameter as e:
            raise click.BadParameter(f"{k}: {e}") from e

    def shell_complete(
        self, ctx: click.Context, param: click.Parameter, incomplete: str
    ) -> list[CompletionItem]:
        """Complete choices that start with the incomplete value."""
        if "=" not in incomplete:
            matched = (
                c + "=" for c in self.OPTIONS if c.startswith(incomplete)
            )
        else:
            k, v = incomplete.split("=", maxsplit=1)

            if k not in self.OPTIONS:
                return []

            items = self.CONVERTERS[k].shell_complete(ctx, param, v)
            matched = (f"{k}={i.value}" for i in items)

        return [CompletionItem(c) for c in matched]

    def get_metavar(self, param: click.Parameter) -> str:
        """Get the metavar for this option."""
        return "|".join([f"{o}=VALUE" for o in self.OPTIONS])


@click.group()
def cli():
    """Binary indexer."""
    pass


@cli.command()
@_common_options(index_must_exist=False)
@click.option("-c", "--use-compilation-database", is_flag=True)
@click.option(
    "-o",
    "--opt",
    multiple=True,
    type=IndexingOptionParamType(),
    help="Set indexing options (key=value).",
)
def index(directory, index_path, opt, use_compilation_database):
    """Index the specified directory."""
    options = IndexingOptions(**dict(opt))

    try:
        stats = index_binary_directory(
            directory,
            index_path,
            options=options,
            use_compilation_database=use_compilation_database,
        )
    except BinaryDirectory.CompilationDatabaseNotFoundError as e:
        error(str(e))
        exit(1)

    log(
        f"Files indexed: {stats.num_files_indexed} "
        f"(out of {stats.num_files_changed} changed files)"
    )
    log(f"Files removed from index: {stats.num_files_deleted}")
    log(f"Symbols indexed: {stats.num_symbols_indexed}")


class SearchOutputFormatParamType(click.Choice):
    """Click parameter type for search --format."""

    OPTIONS = [
        "json",
        "sexp",
        # Add the default Python format as an example
        "{basename}: {name}",
    ]

    def __init__(self):
        """Initialize this param type instance."""
        super().__init__(list(self.OPTIONS))

    def convert(self, value, param, ctx):
        """Convert the given value to correct type, or error out."""
        return value


@cli.command()
@_common_options(index_must_exist=True)
@click.argument(
    "query",
    nargs=-1,
)
@click.option(
    "-n",
    "--num",
    help="Limit the number of results",
    type=click.IntRange(1),
    metavar="LIMIT",
    default=None,
)
@click.option(
    "-f",
    "--format",
    help="Output format (json, sexp, or Python string format)",
    type=SearchOutputFormatParamType(),
    nargs=1,
    default=None,
)
@click.option(
    "--demangle-names/--no-demangle-names",
    default=False,
    help="Make demangled C++ name available as {demangled} format field",
)
def search(_directory, index_path, query, num, format, demangle_names):
    """Search binary directory for symbols."""
    outdated_paths = set()

    queue: list[Symbol] = list()

    name_demangler = NameDemangler()

    @lru_cache
    def is_outdated(symbol: Symbol):
        try:
            os_mtime = symbol.path.stat().st_mtime_ns
        except Exception:
            os_mtime = 0
        return os_mtime != symbol.mtime

    def print_symbol(symbol: Symbol):
        def valueconv(v):
            if isinstance(v, Enum):
                return v.name

            try:
                json.dumps(v)
                return v
            except Exception:
                return str(v)

        data = {
            k: valueconv(v)
            for k, v in {
                "basename": symbol.path.name,
                **asdict(symbol),
            }.items()
        }
        if demangle_names:
            data["demangled"] = name_demangler.get_demangled_name(symbol.name)

        if format is None:
            fmt = "{basename}: {name}"
        else:
            fmt = format

        if fmt == "json":
            del data["basename"]
            click.echo(json.dumps(data))
        elif fmt == "sexp":
            del data["basename"]
            click.echo(sexp_format(data))
        else:
            try:
                click.echo(fmt.format(**data))
            except (KeyError, ValueError, TypeError):
                error(
                    f"Invalid format: '{fmt}'\n"
                    f"Available keys: {list(data.keys())}"
                )
                exit(1)

            if is_outdated(symbol):
                outdated_paths.add(symbol.path)

    def flush_queue():
        while queue:
            item = queue.pop(0)
            print_symbol(item)

    def callback(symbol: Symbol):
        if demangle_names:
            name_demangler.demangle_async(symbol.name)
        queue.append(symbol)
        if len(queue) >= 128:
            flush_queue()

    try:
        with name_demangler:
            search_index(
                index_path=index_path,
                query=" ".join(query),
                limit=num,
                consumer=callback,
            )
            flush_queue()
    except QueryParser.Error as e:
        error(f"Invalid query: {str(e)}")
        exit(1)

    if outdated_paths:
        log(
            (
                "Warning: {} or more files are newer than index,"
                " run `index` command to re-index"
            ),
            len(outdated_paths),
        )


@cli.command()
@_common_options(index_must_exist=True)
def files(_directory, index_path):
    """List all indexed files in a binary directory."""
    with SymbolIndex.open(index_path, readonly=True) as index:
        for path in index.all_files():
            print(path)


if have_graphs:
    from bdx.graph import GraphAlgorithm, generate_graph

    class GraphAlgorithmParamType(click.Choice):
        """Click parameter type for graph --algorithm."""

        OPTIONS = GraphAlgorithm.__members__

        def __init__(self):
            """Initialize this param type instance."""
            super().__init__(list(self.OPTIONS))

        def convert(self, value, param, ctx):
            """Convert the given value to correct type, or error out."""
            return GraphAlgorithm(super().convert(value, param, ctx))

    @cli.command()
    @_common_options(index_must_exist=True)
    @click.argument(
        "start_query",
        nargs=1,
    )
    @click.argument(
        "goal_query",
        nargs=1,
    )
    @click.option(
        "-n",
        "--num-routes",
        type=click.IntRange(min=0),
        default=1,
        help="Generate at most N routes (0=infinity)",
    )
    @click.option(
        "-a",
        "--algorithm",
        type=GraphAlgorithmParamType(),
        default="ASTAR",
        help="The algorithm to choose",
    )
    @click.option(
        "--demangle-names/--no-demangle-names",
        default=True,
        help="Use c++filt to demangle C++ names and use them as node labels.",
    )
    @click.option(
        "--json-progress",
        is_flag=True,
        help=(
            "Print progress to stderr using json"
            " instead of using a progress bar."
        ),
    )
    def graph(
        _directory,
        index_path,
        start_query,
        goal_query,
        num_routes,
        algorithm,
        demangle_names,
        json_progress,
    ):
        """Generate a reference graph in DOT format from two queries.

        For all symbols that match START_QUERY, this command will find
        paths to symbols that match GOAL_QUERY, and generate a graph
        with these two groups as clusters, connected by intermediate
        nodes.

        This can be used to visualize how a symbol is referenced
        throughout a codebase.

        """
        if json_progress:

            num_symbols_visited = 0
            num_routes_found = 0
            last_symbol_print_time = 0.0

            def print_symbols_visited():
                nonlocal last_symbol_print_time

                json.dump(
                    {"visited": num_symbols_visited},
                    sys.stderr,
                )
                log("")
                last_symbol_print_time = time.time()

            def on_symbol_visited():
                nonlocal num_symbols_visited

                num_symbols_visited += 1
                if time.time() - last_symbol_print_time >= 1:
                    print_symbols_visited()

            def on_route_found():
                nonlocal num_routes_found
                num_routes_found += 1
                json.dump(
                    {"found": num_routes_found},
                    sys.stderr,
                )
                log("")

            def on_progress(num_done, num_total):
                json.dump(
                    {"done": num_done, "total": num_total},
                    sys.stderr,
                )
                log("")
                if num_done == num_total:
                    print_symbols_visited()

        else:
            progress_bar = make_progress_bar(unit="nodes")
            visit_progress_bar = make_progress_bar(
                desc="Nodes visited", unit="symbols"
            )
            found_routes_progress_bar = make_progress_bar(
                desc="Found", unit="routes"
            )

            on_symbol_visited = visit_progress_bar.update
            on_route_found = found_routes_progress_bar.update

            def on_progress(num_done, num_total):
                progress_bar.total = num_total
                progress_bar.update()

        graph = generate_graph(
            index_path,
            start_query,
            goal_query,
            num_routes=num_routes if num_routes else None,
            algo=algorithm,
            demangle_names=demangle_names,
            on_progress=on_progress,
            on_symbol_visited=on_symbol_visited,
            on_route_found=on_route_found,
        )
        print(graph)


if __name__ == "__main__":
    cli()

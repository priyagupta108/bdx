from __future__ import annotations

import json
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from sys import exit, stderr, stdout
from typing import Optional

import click

from bdx.binary import BinaryDirectory, Symbol, find_compilation_database
from bdx.index import SymbolIndex, index_binary_directory, search_index
from bdx.query_parser import QueryParser


def guess_directory_from_index_path(
    index_path: Optional[Path],
) -> Optional[Path]:
    """Return the path to the binary directory for given index path."""
    if index_path is not None and Path(index_path).exists():
        try:
            with SymbolIndex(index_path, readonly=True) as index:
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
        @click.pass_context
        @wraps(f)
        def inner(
            ctx: click.Context,
            *args,
            directory: str | Path,
            index_path: str | Path,
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
                    index = SymbolIndex(index_path, readonly=True)
                    indexed_dir = index.binary_dir()
                    if indexed_dir is not None and indexed_dir != directory:
                        msg = (
                            f"Index is for different directory: {indexed_dir}"
                        )
                        raise click.BadParameter(msg)
                except SymbolIndex.Error as e:
                    msg = f"Invalid index: {index_path}"
                    raise click.BadParameter(msg) from e
            elif index_must_exist:
                msg = f"Directory is not indexed: {directory}"
                raise click.BadParameter(msg)

            if did_guess_directory:
                click.echo(
                    f"note: Using {directory} as binary directory",
                    file=stderr,
                )

            f(*args, directory, index_path, **kwargs)

        return inner

    return decorator


@click.group()
def cli():
    """Binary indexer."""
    pass


@cli.command()
@_common_options(index_must_exist=False)
@click.option("-c", "--use-compilation-database", is_flag=True)
def index(directory, index_path, use_compilation_database):
    """Index the specified directory."""
    try:
        stats = index_binary_directory(
            directory,
            index_path,
            use_compilation_database,
        )
    except BinaryDirectory.CompilationDatabaseNotFoundError as e:
        click.echo(f"error: {e}", file=stderr)
        exit(1)

    click.echo(
        f"Files indexed: {stats.num_files_indexed} "
        f"(out of {stats.num_files_changed} changed files)"
    )
    click.echo(f"Files removed from index: {stats.num_files_deleted}")
    click.echo(f"Symbols indexed: {stats.num_symbols_indexed}")


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
    help="Output format",
    nargs=1,
    default=None,
)
def search(_directory, index_path, query, num, format):
    """Search binary directory for symbols."""

    def callback(symbol: Symbol):
        def valueconv(v):
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

        if format is None:
            fmt = "{basename}: {name}"
        else:
            fmt = format

        if fmt == "json":
            del data["basename"]
            json.dump(data, stdout)
            print()
        else:
            try:
                print(fmt.format(**data))
            except (KeyError, ValueError, TypeError):
                click.echo(f"Invalid format: '{fmt}'", file=stderr)
                click.echo(f"Available keys: {list(data.keys())}", file=stderr)
                exit(1)

    try:
        search_index(
            index_path=index_path,
            query=" ".join(query),
            limit=num,
            consumer=callback,
        )
    except QueryParser.Error as e:
        click.echo(f"Invalid query: {str(e)}")
        exit(1)


@cli.command()
@_common_options(index_must_exist=True)
def files(_directory, index_path):
    """List all indexed files in a binary directory."""
    with SymbolIndex(index_path, readonly=True) as index:
        for path in index.all_files():
            print(path)


if __name__ == "__main__":
    cli()

import json
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from sys import exit, stderr, stdout

import click

from bdx.binary import Symbol
from bdx.index import SymbolIndex, index_binary_directory, search_index
from bdx.query_parser import QueryParser


def _common_options(index_must_exist=False):

    def decorator(f):

        did_guess_directory = False

        def default_directory(ctx, _param, value):
            if value:
                return value

            index_path = None

            if "index_path" in ctx.params:
                index_path = Path(ctx.params["index_path"])
            else:
                path = Path().absolute()
                while path.parent != path:
                    maybe_index = SymbolIndex.default_path(path)
                    if maybe_index.exists():
                        index_path = maybe_index
                        break
                    path = path.parent

            if index_path is not None and index_path.exists():
                with SymbolIndex(index_path) as index:
                    binary_dir = index.binary_dir()

                    nonlocal did_guess_directory
                    did_guess_directory = True

                    if binary_dir is not None:
                        return binary_dir

            raise click.MissingParameter("Could not guess it.")

        def default_index_path(ctx, _param, value):
            return value or SymbolIndex.default_path(ctx.params["directory"])

        @click.option(
            "-d",
            "--directory",
            type=click.Path(
                exists=True,
                dir_okay=True,
                file_okay=False,
                resolve_path=True,
            ),
            help="Path to the binary directory..",
            callback=default_directory,
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
            callback=default_index_path,
        )
        @wraps(f)
        def inner(
            *args, directory: str | Path, index_path: str | Path, **kwargs
        ):
            directory = Path(directory)
            index_path = Path(index_path)

            if index_must_exist:
                if not index_path.exists():
                    msg = f"Directory is not indexed: {directory}"
                    raise click.BadParameter(msg)
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
def index(directory, index_path):
    """Index the specified directory."""
    stats = index_binary_directory(directory, index_path)

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
    with SymbolIndex(index_path) as index:
        for path in index.all_files():
            print(path)


if __name__ == "__main__":
    cli()

import os.path
import sys
from pathlib import Path

import click

VERBOSITY = 0


def log(msg, *args):
    """Print ``msg`` to stderr."""
    try:

        def prettify_arg(arg):
            if isinstance(arg, Path) and arg.is_absolute():
                return os.path.relpath(arg)

            return arg

        pretty_args = [prettify_arg(p) for p in args]

        click.echo(msg.format(*pretty_args), file=sys.stderr)
    except Exception as e:
        print(f"Logging error: {e}", file=sys.stderr)
        print(msg, file=sys.stderr)


def info(msg, *args):
    """Log ``msg`` if verbosity level is set high enough."""
    if VERBOSITY >= 1:
        log(msg, *args)


def debug(msg, *args):
    """Log ``msg`` if verbosity level is set high enough."""
    if VERBOSITY >= 2:
        log(msg, *args)


def trace(msg, *args):
    """Log ``msg`` if verbosity level is set high enough."""
    if VERBOSITY >= 3:
        log(msg, *args)


def detail_log(msg, *args):
    """Log ``msg`` if verbosity level is set high enough."""
    if VERBOSITY >= 4:
        log(msg, *args)


def error(msg, *args):
    """Log ``msg`` and then quit the program."""
    log("error: " + msg, *args)
    sys.exit(1)

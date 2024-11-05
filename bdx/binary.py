from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Iterator, Optional

from elftools.elf.elffile import ELFFile

from bdx import info, trace


@dataclass(frozen=True)
class Symbol:
    """Represents a symbol in a binary file."""

    path: Path
    name: str
    section: str
    size: int


def read_symtable(file: str | Path) -> list[Symbol]:
    """Get a symtable from the given file."""
    with open(file, "rb") as f, ELFFile(f) as elf:
        symtab = elf.get_section_by_name(".symtab")

        symbols = []
        for symbol in symtab.iter_symbols():  # pyright: ignore
            size = symbol.entry["st_size"]

            try:
                section = elf.get_section(symbol.entry["st_shndx"]).name
            except Exception:
                section = ""

            symbols.append(
                Symbol(
                    path=Path(file),
                    name=symbol.name,
                    section=section,
                    size=size,
                )
            )

        return symbols


def find_compilation_database(path: Path) -> Optional[Path]:
    """Find the compilation db file in ``path`` or any of it's parent dirs."""
    for dir in [path, *path.parents]:
        file = dir / "compile_commands.json"
        if file.exists():
            return file
    return None


def is_readable_elf_file(path: Path) -> bool:
    """Return true if ``path`` points to an ELF file with read permissions."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            return magic == b"\x7fELF"
    except IOError:
        return False


@dataclass(frozen=True)
class BinaryDirectory:
    """Represents a directory containing zero or more binary files."""

    path: Path
    last_mtime: datetime = datetime.fromtimestamp(0)
    previous_file_list: list[Path] = field(repr=False, default_factory=list)
    use_compilation_database: bool = False

    class CompilationDatabaseNotFoundError(FileNotFoundError):
        """Could not find the compilation database."""

    @cached_property
    def compilation_database(self):
        """The nearest compilation database for this directory."""
        compdb = find_compilation_database(self.path)
        if compdb is not None:
            info("Found compilation database: {}", compdb)
        return compdb

    def changed_files(self) -> Iterator[Path]:
        """Yield files that were changed/created since last run."""
        files = set(self._find_files())
        previous_state = set(self.previous_file_list)

        for path in files:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            is_new = path not in previous_state
            is_changed = not is_new and self.last_mtime < mtime

            trace(
                "{}: is_new={} is_changed={} mtime={}",
                path,
                is_new,
                is_changed,
                mtime,
            )

            if is_new or is_changed:
                yield path

    def deleted_files(self) -> Iterator[Path]:
        """Yield files that were deleted since last run."""
        files = set(self._find_files())
        previous_state = set(self.previous_file_list)

        deleted = previous_state.difference(files)
        yield from deleted

    def _find_files(self) -> Iterator[Path]:
        if self.use_compilation_database:
            yield from self._find_files_from_compilation_database()
        else:
            for file in self.path.rglob("*.o"):
                if is_readable_elf_file(file):
                    yield file
                else:
                    trace("{}: Ignoring, Not a readable ELF file", file)

    def _find_files_from_compilation_database(self) -> Iterator[Path]:
        path = self.compilation_database
        if not path:
            msg = (
                f"compile_commands.json file not found in {path} "
                "or any of the parent directories"
            )
            raise BinaryDirectory.CompilationDatabaseNotFoundError(msg)

        with open(path, "r") as f:
            data = json.load(f)

        for entry in data:
            directory = Path(entry.get("directory", path.parent))
            file = None
            source_file = Path(entry["file"])
            trace("For source file {}", source_file)

            if "output" in entry:
                file = Path(entry["output"])
                trace("  Found binary file in 'output': {}", file)
            elif "command" in entry:
                command = entry["command"]
                match = re.match(".* -o *([^ ]+).*", command)
                if match:
                    file = Path(match.group(1))
                    trace("  Found binary file in 'command': {}", file)
            elif "arguments" in entry:
                args = entry["arguments"]
                for prev, next in zip(args, args[1:]):
                    if prev == "-o":
                        file = Path(next)
                        trace("  Found binary file in 'arguments': {}", file)
                        break

            if not file:
                file = directory / (source_file.stem + ".o")
                trace("  Assuming {} is binary", file)
            if not file.is_absolute():
                file = directory / file

            if is_readable_elf_file(file):
                yield file

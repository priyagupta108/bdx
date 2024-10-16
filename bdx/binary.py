from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from elftools.elf.elffile import ELFFile


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


@dataclass(frozen=True)
class BinaryDirectory:
    """Represents a directory containing zero or more BinaryFiles."""

    path: Path
    last_mtime: datetime = datetime.fromtimestamp(0)
    previous_file_list: list[Path] = field(repr=False, default_factory=list)

    def changed_files(self) -> Iterator[Path]:
        """Yield files that were changed/created since last run."""
        files = set(self._find_files())
        previous_state = set(self.previous_file_list)

        for path in files:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            is_new = path not in previous_state
            is_changed = not is_new and self.last_mtime < mtime

            if is_new or is_changed:
                yield path

    def deleted_files(self) -> Iterator[Path]:
        """Yield files that were deleted since last run."""
        files = set(self._find_files())
        previous_state = set(self.previous_file_list)

        deleted = previous_state.difference(files)
        yield from deleted

    def _find_files(self) -> Iterator[Path]:
        yield from self.path.rglob("*.o")

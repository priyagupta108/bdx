from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from functools import cache, cached_property, total_ordering
from pathlib import Path
from subprocess import Popen, check_output
from typing import IO, Iterator, Optional

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import Relocation, RelocationSection
from elftools.elf.sections import Symbol as ELFSymbol
from elftools.elf.sections import SymbolTableSection
from sortedcontainers import SortedList

from bdx import info, trace


class NameDemangler:
    """A class for batch async demangling of names with c++filt program."""

    def __init__(self):
        """Create a new demangler, with no process.

        The process is started in ``demangle_async``.
        """
        self._process: Optional[Popen] = None
        self._dict = {}

    def __enter__(self) -> "NameDemangler":
        return self

    def __exit__(self, *_args, **_kwargs):
        self._close()

    def demangle_async(self, name: str) -> None:
        """Send ``name`` for demangling.

        The result can be retrieved with ``get_demangled_name``.
        """
        if not self._process:
            self._process = Popen(
                ["c++filt"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )

        stdin: IO = self._process.stdin  # type: ignore

        stdin.write(json.dumps({f"MANGLED_{name}": name}).encode())
        stdin.write(b"\n")

    def get_demangled_name(self, mangled_name: str) -> Optional[str]:
        """Get a demangled name that was mangled in ``demangle_async``."""
        if self._process:
            stdout, _stderr = self._process.communicate()
            for line in stdout.splitlines():
                d = json.loads(line)
                self._dict.update(d)
            self._close()

        return self._dict.get(f"MANGLED_{mangled_name}")

    def _close(self):
        if self._process:
            self._process.terminate()
            self._process.wait(0.5)
            self._process.kill()
        self._process = None


class SymbolType(Enum):
    """Enumeration for recognized ELF symbol types (STT_* values)."""

    NOTYPE = 0
    OBJECT = 1
    FUNC = 2
    SECTION = 3
    FILE = 4
    COMMON = 5
    TLS = 6
    NUM = 7
    RELC = 8
    SRELC = 9
    LOOS = 10
    LOOS_PLUS_ONE = 11
    HIOS = 12
    LOPROC = 13
    LOPROC_PLUS_ONE = 14
    HIPROC = 15

    @staticmethod
    def of_elf_symbol(symbol: ELFSymbol) -> "SymbolType":
        """Return a symbol type from saved ST_INFO value."""
        stt_type = symbol["st_info"]["type"]  # STT_* of Elf

        # TODO: Recognize LOOS+1 and LOPROC+1

        try:
            return SymbolType[stt_type[len("STT_") :]]
        except Exception:
            return SymbolType.NOTYPE


@total_ordering
@dataclass(frozen=True, order=False)
class Symbol:
    """Represents a symbol in a binary file."""

    path: Path
    source: Optional[Path]
    name: str
    section: str
    address: int
    size: int
    type: SymbolType
    relocations: list[str] = field(hash=False)
    mtime: int

    def __lt__(self, other):
        return self.address < other.address

    def demangle_name(self):
        """Return the demangled name."""
        try:
            return check_output(["c++filt", self.name]).decode().strip()
        except Exception:
            return self.name


class CompilationDatabase:
    """Interface for retrieving data from ``compile_commands.json`` file."""

    def __init__(self, path: Path):
        """Construct a compilation database from file located at ``path``."""
        self._path = path
        self._source_to_binary: dict[Path, Path] = {}
        self._binary_to_source: dict[Path, Path] = {}

        self._read()

    def get_source_file_for_binary(self, binary: Path) -> Optional[Path]:
        """Return the source file for given binary file."""
        return self._binary_to_source.get(binary)

    def get_binary_for_source_file(self, source: Path) -> Optional[Path]:
        """Return the binary file for given source file."""
        return self._source_to_binary.get(source)

    def get_all_binary_files(self) -> list[Path]:
        """Get all known binary files."""
        return list(self._binary_to_source.keys())

    def _read(self):
        path = self._path

        with open(path) as f:
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

            self._source_to_binary[source_file] = file
            self._binary_to_source[file] = source_file


@cache
def _read_compdb(path: Path, _mtime: int) -> CompilationDatabase:
    return CompilationDatabase(path)


def _find_source_file_compdb(elf: ELFFile) -> Optional[Path]:
    binary_path = Path(elf.stream.name)
    compdb_path = find_compilation_database(binary_path.parent)
    if not compdb_path:
        return None

    compdb = _read_compdb(compdb_path, compdb_path.stat().st_mtime_ns)
    return compdb.get_source_file_for_binary(binary_path)


def _find_source_file_dwarfdump(elf: ELFFile) -> Optional[Path]:
    try:
        out = subprocess.check_output(
            ["dwarfdump", "-r", elf.stream.name]
        ).decode()
    except Exception:
        return None

    match = re.match(
        ".*DW_AT_name *([^\n]+).*DW_AT_comp_dir *([^\n]+).*",
        out,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        return None

    dw_at_name, dw_at_comp_dir = match.groups()

    path = Path(dw_at_comp_dir) / Path(dw_at_name)
    if path.exists():
        return path.resolve()
    return None


def _find_source_file(
    elf: ELFFile,
    use_compilation_database: bool,
    use_dwarfdump: bool,
) -> Optional[Path]:
    path = None

    if use_compilation_database:
        path = _find_source_file_compdb(elf)

    if not path and use_dwarfdump:
        path = _find_source_file_dwarfdump(elf)

    if path:
        trace("Found source file for {}: {}", Path(elf.stream.name), path)
    else:
        trace("Could not find source file for {}", Path(elf.stream.name))

    return path


def _read_symbols_in_file(
    file: Path,
    elf: ELFFile,
    min_symbol_size: int,
    use_compilation_database: bool,
    use_dwarfdump: bool,
) -> list[Symbol]:
    symtab = elf.get_section_by_name(".symtab")
    if not isinstance(symtab, SymbolTableSection):
        msg = ".symtab is not a SymbolTableSection"
        raise RuntimeError(msg)

    mtime = os.stat(elf.stream.fileno()).st_mtime_ns
    source = _find_source_file(
        elf,
        use_compilation_database,
        use_dwarfdump,
    )

    symbols = []
    for symbol in symtab.iter_symbols():
        size = symbol["st_size"]
        if size < min_symbol_size:
            continue

        try:
            section = elf.get_section(symbol["st_shndx"]).name
        except Exception:
            section = ""
        symbols.append(
            Symbol(
                path=Path(file),
                source=source,
                name=symbol.name,
                section=section,
                address=symbol["st_value"],
                size=size,
                type=SymbolType.of_elf_symbol(symbol),
                relocations=list(),
                mtime=mtime,
            )
        )

    return symbols


def _find_relocation_target(
    reloc: Relocation, symlist: SortedList[Symbol]
) -> Optional[Symbol]:
    if not symlist:
        return None

    address = reloc["r_offset"]
    index = symlist.bisect_left(replace(symlist[0], address=address))

    possibilities = symlist[max(index - 1, 0) : index + 1]
    for symbol in possibilities:
        start = symbol.address
        end = start + symbol.size
        if start <= address < end:
            return symbol

    return None


def _read_relocations(elf: ELFFile, symbols: list[Symbol]):
    symbols_by_section: dict[str, SortedList[Symbol]] = defaultdict(SortedList)
    for sym in symbols:
        symbols_by_section[sym.section].add(sym)

    for reloc_section in elf.iter_sections():
        if not isinstance(reloc_section, RelocationSection):
            continue

        section = elf.get_section(reloc_section["sh_info"])
        symtable = elf.get_section(reloc_section["sh_link"])
        symlist = symbols_by_section[section.name]

        if not isinstance(symtable, SymbolTableSection):
            msg = (
                f"Section {symtable.name} linked to relocation section "
                f"{reloc_section.name} is not a valid SymbolTableSection"
            )
            raise RuntimeError(msg)

        for reloc in reloc_section.iter_relocations():
            symbol = _find_relocation_target(reloc, symlist)
            if symbol is None:
                continue

            relocated_symbol_name = symtable.get_symbol(
                reloc["r_info_sym"]
            ).name
            symbol.relocations.append(relocated_symbol_name)

    for symbol in symbols:
        refs = list(set(symbol.relocations))
        refs.sort()
        symbol.relocations.clear()
        symbol.relocations.extend(refs)


def read_symbols_in_file(
    file: str | Path,
    with_relocations: bool = True,
    min_symbol_size=1,
    use_compilation_database: bool = True,
    use_dwarfdump: bool = True,
) -> list[Symbol]:
    """Get a symtable from the given file.

    Args:
        file: The binary file to read.
        with_relocations: If True, populate ``relocations`` of each Symbol.
            Setting this to False can significantly speed up reading.
        min_symbol_size: Only return symbols whose size is greater or equal
            to this.
        use_compilation_database: If True, then use compile_commands.json
            file (if it exists) to get the source files for each symbol.
        use_dwarfdump: If True, then use the ``dwarfdump`` program (if it
            exists) to get the source files for each symbol (if available).

    """
    with open(file, "rb") as f, ELFFile(f) as elf:
        symbols = _read_symbols_in_file(
            Path(file),
            elf,
            min_symbol_size=min_symbol_size,
            use_compilation_database=use_compilation_database,
            use_dwarfdump=use_dwarfdump,
        )

        if with_relocations:
            _read_relocations(elf, symbols)

        return symbols


@cache
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

    _file_list: list[Path] = field(repr=False, default_factory=list)

    class CompilationDatabaseNotFoundError(FileNotFoundError):
        """Could not find the compilation database."""

    def __post_init__(self):
        self._file_list.extend(self._find_files())

    @cached_property
    def compilation_database(self):
        """The nearest compilation database for this directory."""
        compdb = find_compilation_database(self.path)
        if compdb is not None:
            info("Found compilation database: {}", compdb)
        return compdb

    def changed_files(self) -> Iterator[Path]:
        """Yield files that were changed/created since last run."""
        files = self._file_list
        previous_state = set(self.previous_file_list)

        for path in sorted(files):
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
        files = set(self._file_list)
        previous_state = set(self.previous_file_list)

        deleted = previous_state.difference(files)
        yield from sorted(deleted)

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

        compdb = _read_compdb(path, path.stat().st_mtime_ns)

        for file in compdb.get_all_binary_files():
            if is_readable_elf_file(file):
                yield file

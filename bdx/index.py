from __future__ import annotations

import multiprocessing as mp
import os
import pickle
import re
import signal
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Iterator, List, Optional

import xapian
from tqdm import tqdm

from bdx import debug, detail_log, log, trace
from bdx.binary import BinaryDirectory, Symbol, read_symtable

MAX_TERM_SIZE = 244


@dataclass
class IndexingOptions:
    """User settings for indexing."""

    num_processes: int = os.cpu_count() or 1
    index_relocations: bool = True
    min_symbol_size: int = 1


@dataclass(frozen=True)
class DatabaseField:
    """Contains information about a schema field."""

    name: str
    prefix: str

    def index(self, document: xapian.Document, value: Any):
        """Index ``value`` in the ``document``."""
        value = self.preprocess_value(value)
        prefix = self.prefix.encode()

        term = prefix + value
        term = term[:MAX_TERM_SIZE]

        document.add_term(term)

    def preprocess_value(self, value: Any) -> bytes:
        """Preprocess the value before indexing it."""
        if not isinstance(value, (str, bytes)):
            value = str(value)
        if isinstance(value, str):
            value = value.encode()
        return value

    def make_query(self, value: str, wildcard: bool = False) -> xapian.Query:
        """Make a query for the given value.

        Args:
            value: The string to search for in the query.
            wildcard: If true, make a wildcard query.

        """
        value = self.preprocess_value(value).decode()
        term = f"{self.prefix}{value}"
        if wildcard:
            return xapian.Query(
                xapian.Query.OP_WILDCARD,
                term,
                0,
                xapian.Query.WILDCARD_LIMIT_FIRST,
            )
        else:
            return xapian.Query(term)


class TextField(DatabaseField):
    """A database field that indexes text."""

    def preprocess_value(self, value: Any) -> bytes:
        """Preprocess the value before indexing it."""
        return super().preprocess_value(value).lower()

    def index(self, document: xapian.Document, value: Any):
        """Index ``value`` in the ``document``."""
        termgen = xapian.TermGenerator()
        termgen.set_document(document)
        termgen.set_max_word_length(MAX_TERM_SIZE - len(self.prefix) - 1)
        termgen.index_text_without_positions(
            self.preprocess_value(value), 1, self.prefix
        )


@dataclass(frozen=True)
class IntegerField(DatabaseField):
    """A database field that indexes integers."""

    slot: int

    def preprocess_value(self, value: Any) -> bytes:
        """Preprocess the value before indexing it."""
        if not isinstance(value, int):
            msg = f"Invalid type for {self.__class__.__name__}: {value}"
            raise TypeError(msg)
        return xapian.sortable_serialise(value)

    def index(self, document: xapian.Document, value: Any):
        """Index ``value`` in the ``document``."""
        document.add_value(self.slot, self.preprocess_value(value))

    def make_query(self, value: str, wildcard: bool = False) -> xapian.Query:
        """Make a query for the given value.

        Args:
            value: The string to search for in the query.  It must hold an
                integer or a xapian range expression: FROM..TO.
            wildcard: Unused.

        """
        match = re.match("([0-9]+)?[.][.]([0-9]+)?|([0-9]+)", value)
        if not match:
            return xapian.Query()

        start, end, eq = match.groups()

        if eq is not None:
            # Exact value
            v = self.preprocess_value(int(eq))
            return xapian.Query(
                xapian.Query.OP_VALUE_RANGE,
                self.slot,
                v,
                v,
            )
        elif start is not None and end is not None:
            return xapian.Query(
                xapian.Query.OP_VALUE_RANGE,
                self.slot,
                (self.preprocess_value(int(start))),
                (self.preprocess_value(int(end))),
            )
        elif start is not None:
            return xapian.Query(
                xapian.Query.OP_VALUE_GE,
                self.slot,
                (self.preprocess_value(int(start))),
            )
        elif end is not None:
            return xapian.Query(
                xapian.Query.OP_VALUE_LE,
                self.slot,
                (self.preprocess_value(int(end))),
            )
        else:
            msg = f"Invalid integer range query: {value}"
            raise ValueError(msg)


class PathField(DatabaseField):
    """Represents a path field in the database."""

    def index(self, document: xapian.Document, value: Any):
        """Index ``value`` in the ``document``."""
        path = Path(value)
        super().index(document, path)

        # Also index the basename
        super().index(document, path.name)

    def make_query(self, value: str, wildcard: bool = False) -> xapian.Query:
        """Make a query for the path in ``value``."""
        query = super().make_query(value, wildcard)

        if not value.startswith("/"):
            try:
                value = str((Path() / value).absolute().resolve())
                rhs_query = super().make_query(value, wildcard)

                query = xapian.Query(xapian.Query.OP_OR, query, rhs_query)

            except ValueError:
                pass

        return query


class RelocationsField(DatabaseField):
    """Represents a field for the list of relocations in a given symbol."""

    def index(self, document: xapian.Document, value: Any):
        """Index ``value`` in the ``document``."""
        if isinstance(value, list):
            for v in value:
                self.index(document, v)
        else:
            return super().index(document, value)


class SymbolNameField(TextField):
    """DatabaseField that indexes symbol names specially."""

    @staticmethod
    def tokenize_value(value: str) -> set[str]:
        """Split given value into tokens for indexing."""
        letters_only = re.findall("[a-zA-Z]{2,}", value)

        # Split "CamelCaseWord" into "Camel Case Word"
        camel_case_words = re.findall("[A-Z][a-z]+", " ".join(letters_only))

        # Find uppercase words
        upper_case_words = re.findall("[A-Z]{2,}", " ".join(letters_only))

        numbers = re.findall("[0-9]+", value)
        words_with_numbers = re.findall("[a-zA-Z]+[0-9]+", value)

        tokens = set()
        for tokenlist in [
            letters_only,
            camel_case_words,
            upper_case_words,
            numbers,
            words_with_numbers,
        ]:
            tokens.update(tokenlist)

        return set(tokens)

    def index(self, document, value: Any):
        """Index ``value`` in the ``document``."""
        DatabaseField.index(self, document, value)

        if isinstance(value, bytes):
            value = value.decode()

        tokens = self.tokenize_value(value)
        value = " ".join(tokens)

        super().index(document, value)


@dataclass(frozen=True)
class Schema(Mapping):
    """Contains information about database fields."""

    fields: List[DatabaseField] = field(default_factory=list)
    _field_dict: Dict[str, DatabaseField] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self):
        """Make a map from the field list."""
        self._field_dict.update({x.name: x for x in self.fields})

    def __getitem__(self, key):
        if not self.fields:
            return DatabaseField(
                name=key,
                prefix=f"X{key.upper()}",
            )
        return self._field_dict[key]

    def __iter__(self):
        return iter(self._field_dict)

    def __len__(self):
        return len(self.fields)

    def index_document(self, document: xapian.Document, **fields: str):
        """Index the ``fields`` in given ``document``."""
        for fieldname, fieldval in fields.items():
            if fieldname not in self:
                continue

            field = self[fieldname]
            field.index(document, fieldval)


class SymbolIndex:
    """Easy interface for a xapian database, with schema support."""

    SCHEMA = Schema(
        [
            PathField("path", "XP"),
            SymbolNameField("name", "XN"),
            DatabaseField("section", "XSN"),
            IntegerField("address", "XA", slot=0),
            IntegerField("size", "XSZ", slot=1),
            RelocationsField("relocations", "XR"),
            IntegerField("mtime", "XM", slot=2),
        ]
    )

    class Error(RuntimeError):
        """General SymbolIndex error."""

    class ClosedError(Error):
        """SymbolIndex is closed error."""

    class TransactionInProgressError(Error):
        """Already in a transaction error."""

    class ReadOnlyError(Error):
        """SymbolIndex is read-only error."""

    class DoesNotExistError(Error):
        """SymbolIndex does not exist error."""

    class SchemaError(Error):
        """SymbolIndex schema error."""

    class ModifiedError(Error):
        """SymbolIndex was modified and should be reopened."""

    def __init__(
        self,
        path: Path,
        readonly: bool,
        is_shard: bool,
    ):
        """Construct a SymbolIndex at given ``path``.

        Do not use this constructor directly - instead, use one of the
        factory functions.

        """
        if not readonly:
            path.mkdir(exist_ok=True, parents=True)

        self._path = path

        try:
            if readonly:
                self._db = xapian.Database(str(path))
            else:
                self._db = xapian.WritableDatabase(str(path))
        except xapian.DatabaseOpeningError as e:
            if not path.is_dir():
                msg = f"SymbolIndex does not exist: {path}"
                raise SymbolIndex.DoesNotExistError(msg) from e
            if not os.access(path, os.R_OK):
                msg = f"SymbolIndex is not readable: {path}"
                raise SymbolIndex.Error(msg) from e
            raise SymbolIndex.Error(e) from e

        schema = self.SCHEMA
        pickled_schema = self.get_metadata("__schema__")
        if pickled_schema:
            saved_schema = pickle.loads(pickled_schema)
            if schema and schema != saved_schema:
                self._db.close()
                raise SymbolIndex.SchemaError(
                    "Schema on disk is different "
                    f"than the one in constructor ({saved_schema} != {schema})"
                )
            schema = saved_schema

        self._shards: list[xapian.Database | xapian.WritableDatabase] = []
        self._is_shard = is_shard
        if not is_shard:
            for shard in self.shards():
                trace("Opening shard: {}", shard)
                if readonly:
                    db = xapian.Database(str(shard))
                else:
                    db = xapian.WritableDatabase(str(shard))
                self._shards.append(db)
                self._db.add_database(db)

        self._schema = schema or Schema()

        if not readonly:
            self.set_metadata("__schema__", pickle.dumps(schema))

    @staticmethod
    def open(directory: Path | str, readonly: bool = False) -> "SymbolIndex":
        """Open a SymbolIndex.

        Args:
            directory: Path to the database directory.
                It will be created if it doesn't exist, except
                if ``readonly``.
            readonly: If False, create a writable database,
                otherwise the database will be read-only.

        """
        index = SymbolIndex(
            Path(directory) / "db", readonly=readonly, is_shard=False
        )

        debug("Opened index: {}", index.path)
        debug("Index has saved binary directory: {}", index.binary_dir())
        debug("Index mtime: {}", index.mtime())
        trace("Index schema: {}", index.schema)

        return index

    @staticmethod
    def open_shard(directory: Path | str) -> "SymbolIndex":
        """Open a writable shard for index in given directory."""
        for path in SymbolIndex.generate_shard_paths(Path(directory) / "db"):
            try:
                return SymbolIndex(path, readonly=False, is_shard=True)
            except Exception:
                pass

        msg = f"Could not open shard for {directory}"
        raise SymbolIndex.Error(msg)

    @staticmethod
    def generate_shard_paths(directory: Path | str) -> Iterator[Path]:
        """Infinitely yield paths to possible shards of this database.

        The shards reside in the same directory, but with different suffix.

        """
        directory = Path(directory).absolute()
        i = 0
        while True:
            yield directory.parent / f"{directory.name}.{i:0>3}"
            i += 1

    def shards(self) -> Iterator[Path]:
        """Yield the shards of this database."""
        for x in self.generate_shard_paths(self.path):
            if x.exists():
                yield x
            else:
                break

    @staticmethod
    def default_path(directory: Path | str) -> Path:
        """Return a default index path for binary ``directory``."""
        parts = Path(directory).absolute().parts[1:]
        global_cache_dir = Path(
            os.getenv("XDG_CACHE_HOME", "~/.cache")
        ).expanduser()
        basename = "!".join(parts)
        return global_cache_dir / "bdx" / "index" / basename

    @property
    def path(self) -> Path:
        """The path of this SymbolIndex."""
        return self._path

    @property
    def schema(self) -> Schema:
        """The schema of this SymbolIndex."""
        return self._schema

    def close(self):
        """Close this SymbolIndex."""
        if self._is_shard:
            trace("Closing shard: {}", self.path)
        else:
            debug("Closing index: {}", self.path)
        self._live_db().close()
        self._db = None

    def __enter__(self):
        self._live_db()
        return self

    def __exit__(self, *_args):
        self.close()

    def get_metadata(self, key: str) -> bytes:
        """Get the metadata associated with given key, or empty bytes obj."""
        if not key:
            msg = "Key must be a non-empty string"
            raise ValueError(msg)
        return self._live_db().get_metadata(key)

    def get_metadata_keys(self) -> Iterator[str]:
        """Yield all metadata keys in this SymbolIndex."""
        for key in self._live_db().metadata_keys():  # pyright: ignore
            yield key.decode()

    def set_metadata(self, key: str, metadata: bytes):
        """Set metadata for the given key."""
        if not key:
            msg = "Key must be a non-empty string"
            raise ValueError(msg)
        self._live_writable_db().set_metadata(key, metadata)

    def mtime(self) -> datetime:
        """Return the max modification time of this index."""
        db = self._live_db()
        field_data = self.schema["mtime"]
        val = db.get_value_upper_bound(field_data.slot)  # pyright: ignore
        if not val:
            return datetime.fromtimestamp(0)

        val_int = xapian.sortable_unserialise(val)
        return datetime.fromtimestamp(val_int / 1e9)

    def binary_dir(self) -> Optional[Path]:
        """Get binary directory of this index, set by ``set_binary_dir``."""
        if "binary_dir" in set(self.get_metadata_keys()):
            return Path(self.get_metadata("binary_dir").decode())
        return None

    def set_binary_dir(self, binary_dir: Path):
        """Set the modification time of this index."""
        self.set_metadata("binary_dir", str(binary_dir).encode())

    @contextmanager
    def transaction(self):
        """Return a context manager for transactions in this SymbolIndex."""
        try:
            self._live_writable_db().begin_transaction()
        except xapian.InvalidOperationError as e:
            msg = "Already inside a transaction"
            raise SymbolIndex.TransactionInProgressError(msg) from e

        try:
            yield None
            self._live_writable_db().commit_transaction()
        except Exception as e:
            debug("Cancel transaction due to error: {}", e)
            self._live_writable_db().cancel_transaction()
            raise

    def add_symbol(self, symbol: Symbol):
        """Add a document to the SymbolIndex."""
        db = self._live_writable_db()
        document = xapian.Document()
        self.schema.index_document(document, **asdict(symbol))
        document.set_data(pickle.dumps(symbol))
        db.add_document(document)

    def delete_file(self, file: Path):
        """Delete all documents for the given file path."""
        term_with_prefix = self.schema["path"].prefix + str(file)
        self._live_writable_db().delete_document(term_with_prefix)

    def all_files(self) -> Iterator[Path]:
        """Yield all the files indexed in this SymbolIndex."""
        db = self._live_db()
        field_data = self.schema["path"]
        all_terms = db.allterms(field_data.prefix)  # pyright: ignore

        for term in all_terms:
            value = term.term[len(field_data.prefix) :]
            path = Path(value.decode())
            if path.is_absolute():
                yield path

    def search(
        self,
        query: str | xapian.Query,
        first: int = 0,
        limit: Optional[int] = None,
    ) -> Iterator[Symbol]:
        """Yield symbols matching the given ``query``."""
        db = self._live_db()

        if limit is None:
            limit = db.get_doccount()

        if isinstance(query, str):
            query = self._parse_query(query)

        debug("Search query: {}", query)

        enquire = xapian.Enquire(db)
        enquire.set_query(query)

        try:
            for match in enquire.get_mset(first, limit):
                document = match.document
                pickled_data = document.get_data()
                yield pickle.loads(pickled_data)
        except xapian.DatabaseModifiedError as e:
            raise SymbolIndex.ModifiedError from e

    def _live_db(self) -> xapian.Database | xapian.WritableDatabase:
        if self._db is None:
            msg = "SymbolIndex is not open"
            raise SymbolIndex.ClosedError(msg)
        return self._db

    def _live_writable_db(self) -> xapian.WritableDatabase:
        db = self._live_db()
        if not isinstance(db, xapian.WritableDatabase):
            msg = "SymbolIndex is open for reading only"
            raise SymbolIndex.ReadOnlyError(msg)
        return db

    def _parse_query(self, query: str) -> xapian.Query:
        from bdx.query_parser import QueryParser

        query_parser = QueryParser(
            SymbolIndex.SCHEMA,
            default_fields=["name"],
            auto_wildcard=True,
        )
        return query_parser.parse_query(query)


@dataclass
class IndexingStats:
    """Contains stats about indexing operation."""

    num_files_indexed: int = 0
    num_files_changed: int = 0
    num_files_deleted: int = 0
    num_symbols_indexed: int = 0


@contextmanager
def sigint_catcher() -> Iterator[Callable[[], bool]]:
    """Context manager that temporarily disables SIGINT exceptions.

    The yielded value is callable.  It returns true if SIGINT was
    signalled.

    """
    original_handler = signal.getsignal(signal.SIGINT)

    called = False

    def handler(*_args):
        nonlocal called
        called = True
        log("Interrupted, press C-c again to exit")
        signal.signal(signal.SIGINT, original_handler)

    def checker():
        return called

    try:
        signal.signal(signal.SIGINT, handler)
        yield checker
    finally:
        signal.signal(signal.SIGINT, original_handler)


@dataclass
class _WorkerContext:
    index: SymbolIndex
    options: IndexingOptions

    instance: ClassVar["_WorkerContext"]

    def run(self):
        with self.index.transaction():
            yield

        self.index.close()


def _index_single_file(file: Path) -> int:
    context = _WorkerContext.instance
    index = context.index
    options = context.options

    try:
        symtab = read_symtable(
            file,
            with_relocations=options.index_relocations,
            min_symbol_size=options.min_symbol_size,
        )
    except Exception as e:
        log("{}: {}: {}", file.name, e.__class__.__name__, str(e))
        return 0

    num = 0

    for symbol in symtab:
        detail_log(
            "Got symbol '{}' in {}, section '{}', size {}, mtime {}",
            symbol.name,
            symbol.path,
            symbol.section,
            symbol.size,
            symbol.mtime,
        )

        index.add_symbol(symbol)

        num += 1

    if num == 0:
        trace("{}: No symbols found", file)
        # Add a single document if there are no symbols.  Otherwise,
        # we would always treat it as unindexed.
        index.add_symbol(
            Symbol(
                path=file,
                name="",
                section="",
                address=0,
                size=0,
                relocations=list(),
                mtime=file.stat().st_mtime_ns,
            )
        )
        num += 1

    trace("{}: Adding {} symbol(s) to index", file, num)

    return num


def _init_pool_worker(
    index_path,
    stop_event,
    barrier,
    options: IndexingOptions,
):
    index = SymbolIndex.open_shard(index_path)
    context = _WorkerContext(index, options)
    runner = context.run()

    next(runner)

    def watchdog_thread():
        stop_event.wait()
        try:
            next(runner)
        except StopIteration:
            pass
        barrier.wait()

    threading.Thread(target=watchdog_thread).start()

    _WorkerContext.instance = context


def index_binary_directory(
    directory: str | Path,
    index_path: Path,
    options: IndexingOptions,
    use_compilation_database: bool = False,
) -> IndexingStats:
    """Index the given directory."""
    stats = IndexingStats()
    debug("Options: {}", options)

    bindir_path = Path(directory)

    with SymbolIndex.open(index_path, readonly=False) as index:
        if index.binary_dir() is None:
            index.set_binary_dir(bindir_path)

        mtime = index.mtime()
        existing_files = list(index.all_files())
        bdir = BinaryDirectory(
            bindir_path,
            mtime,
            existing_files,
            use_compilation_database=use_compilation_database,
        )

        changed_files = list(bdir.changed_files())
        deleted_files = list(bdir.deleted_files())

        changed_files.sort()
        deleted_files.sort()

        stats.num_files_changed = len(changed_files)
        stats.num_files_deleted = len(deleted_files)

        for file in changed_files:
            index.delete_file(file)
            debug("File modified: {}", file)
        for file in deleted_files:
            index.delete_file(file)
            debug("File deleted: {}", file)

    num_processes = options.num_processes
    stop_event = mp.Event()
    barrier = mp.Barrier(num_processes + 1)

    with (
        sigint_catcher() as interrupted,
        mp.Pool(
            processes=num_processes,
            initializer=_init_pool_worker,
            initargs=[index_path, stop_event, barrier, options],
        ) as pool,
    ):
        perfile_iterator = pool.imap_unordered(
            _index_single_file, changed_files
        )

        iterator = tqdm(
            perfile_iterator, unit="file", total=len(changed_files)
        )

        for num in iterator:
            stats.num_files_indexed += 1
            stats.num_symbols_indexed += num

            if interrupted():
                log("Interrupted, exiting")
                break

        stop_event.set()
        barrier.wait()

    return stats


def search_index(
    index_path: Path,
    query: str,
    consumer: Callable[[Symbol], None],
    limit: Optional[int] = None,
):
    """Search the given index."""
    if not query:
        query = "*:*"

    with SymbolIndex.open(index_path, readonly=True) as index:
        for symbol in index.search(query, limit=limit):
            consumer(symbol)

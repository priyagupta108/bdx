import multiprocessing as mp
import os
import pickle
import re
import signal
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import xapian
from tqdm import tqdm

from bdx.binary import BinaryDirectory, Symbol, read_symtable

MAX_TERM_SIZE = 244


@dataclass(frozen=True)
class DatabaseField:
    """Contains information about a schema field."""

    name: str
    prefix: str
    boolean: bool = False
    search_only: bool = False
    lowercase: bool = True

    def index(self, document: xapian.Document, value: Any):
        """Index ``value`` in the ``document``."""
        value = self.preprocess_value(value)
        prefix = self.prefix.encode()

        term = prefix + value

        if len(term) > MAX_TERM_SIZE:
            return

        document.add_term(term)

    def preprocess_value(self, value: Any) -> bytes:
        """Preprocess the value before indexing it.

        This will e.g. make the value lowercase.
        """
        if not isinstance(value, (str, bytes)):
            value = str(value)
        if isinstance(value, str):
            value = value.encode()
        if self.lowercase and not self.boolean:
            value = value.lower()
        return value


class TextField(DatabaseField):
    """A database field that indexes text."""

    def index(self, document: xapian.Document, value: Any):
        """Index ``value`` in the ``document``."""
        termgen = xapian.TermGenerator()
        termgen.set_document(document)
        termgen.set_max_word_length(MAX_TERM_SIZE - len(self.prefix) - 1)
        termgen.index_text(self.preprocess_value(value), 1, self.prefix)


@dataclass(frozen=True)
class IntegerField(DatabaseField):
    """A database field that indexes integers."""

    slot: int = 0

    def preprocess_value(self, value: Any) -> bytes:
        """Preprocess the value before indexing it."""
        if not isinstance(value, int):
            msg = f"Invalid type for {self.__class__.__name__}: {value}"
            raise TypeError(msg)
        return xapian.sortable_serialise(value)

    def index(self, document: xapian.Document, value: Any):
        """Index ``value`` in the ``document``."""
        document.add_value(self.slot, self.preprocess_value(value))


class SymbolNameField(TextField):
    """DatabaseField that indexes symbol names specially."""

    def index(self, document, value: Any):
        """Index ``value`` in the ``document``."""
        DatabaseField.index(self, document, value)

        if isinstance(value, bytes):
            value = value.decode()
        value = re.sub("[^a-zA-Z]+", " ", value)
        value += re.sub("([A-Z]+)", " \\1", value)

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

    def serialize_document(self, fields: dict[str, Any]) -> bytes:
        """Make a xapian document from ``fields`` and serialize it to bytes."""
        document = xapian.Document()
        self.index_document(document, **fields)
        serialized_document = document.serialise()

        return pickle.dumps((serialized_document, fields))


class SymbolIndex:
    """Easy interface for a xapian interface, with schema support."""

    SCHEMA = Schema(
        [
            DatabaseField("path", "XP", lowercase=False),
            SymbolNameField("name", "XN"),
            TextField("section", "XSN"),
            IntegerField("size", "XSZ", slot=0),
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

    class QueryParserError(Error):
        """Error in the query."""

    def __init__(
        self,
        path: Path,
        readonly: bool = False,
    ):
        """Construct a SymbolIndex at given ``path``.

        Args:
            path: Path to the database directory.
                  It will be created if it doesn't exist, except
                  if ``readonly``.
            readonly: If False, create a writable database,
                  otherwise the database will be read-only.

        """
        if not readonly:
            path.mkdir(exist_ok=True, parents=True)

        self._path = path

        try:
            if readonly:
                self._db = xapian.Database(str(path))
            else:
                path.parent.mkdir(exist_ok=True)
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

        self._schema = schema or Schema()

        if not readonly:
            self.set_metadata("__schema__", pickle.dumps(schema))

    @staticmethod
    def default_path(directory: Path | str) -> Path:
        """Return a default index path for binary ``directory``."""
        parts = Path(directory).parts[1:]
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
        """Return the modification time of this index, set by ``set_mtime``."""
        if "mtime" in set(self.get_metadata_keys()):
            return pickle.loads(self.get_metadata("mtime"))
        else:
            return datetime.fromtimestamp(0)

    def set_mtime(self, mtime: datetime):
        """Set the modification time of this index."""
        self.set_metadata("mtime", pickle.dumps(mtime))

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
        except Exception:
            self._live_writable_db().cancel_transaction()
            raise

    @staticmethod
    def serialize_symbol(symbol: Symbol) -> bytes:
        """Serialize a symbol for adding it later."""
        return SymbolIndex.SCHEMA.serialize_document(asdict(symbol))

    def add_serialized_document(self, serialized_document: bytes):
        """Add a document to the SymbolIndex.

        To serialize a document, use the ``serialize_symbol`` function.
        """
        db = self._live_writable_db()

        serialized_document, fields = pickle.loads(serialized_document)

        document = xapian.Document.unserialise(serialized_document)
        self._set_document_data(document, fields)
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
            yield Path(value.decode())

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

        enquire = xapian.Enquire(db)
        enquire.set_query(query)

        try:
            for match in enquire.get_mset(first, limit):
                document = match.document
                pickled_data = document.get_data()
                data = pickle.loads(pickled_data)
                yield Symbol(**data)
        except xapian.DatabaseModifiedError as e:
            raise SymbolIndex.ModifiedError from e

    def _set_document_data(self, document: xapian.Document, fields):
        """Set document data, omitting search_only fields."""
        for schema_field in self._schema.fields:
            name = schema_field.name
            if name in fields and schema_field.search_only:
                del fields[name]

        document.set_data(pickle.dumps(fields))

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
        if query == "*:*":
            return xapian.Query.MatchAll  # pyright: ignore

        parser = xapian.QueryParser()
        parser.set_default_op(xapian.Query.OP_AND)
        parser.set_database(self._live_db())

        for schema_field in self.schema.values():
            if isinstance(schema_field, IntegerField):
                vrp = xapian.NumberValueRangeProcessor(
                    schema_field.slot, schema_field.name + ":", True
                )
                parser.add_valuerangeprocessor(vrp)
            elif schema_field.boolean:
                parser.add_boolean_prefix(
                    schema_field.name, schema_field.prefix
                )
            else:
                parser.add_prefix(schema_field.name, schema_field.prefix)

        try:
            parsed_query = parser.parse_query(
                query, parser.FLAG_DEFAULT | parser.FLAG_WILDCARD
            )
        except xapian.QueryParserError as e:
            raise SymbolIndex.QueryParserError(e) from e
        return parsed_query


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
        print("Interrupted, press C-c again to exit", file=sys.stderr)
        signal.signal(signal.SIGINT, original_handler)

    def checker():
        return called

    try:
        signal.signal(signal.SIGINT, handler)
        yield checker
    finally:
        signal.signal(signal.SIGINT, original_handler)


def _read_and_serialize_symtable(file: Path) -> list[bytes]:
    ret = []
    try:
        symtab = read_symtable(file)
    except Exception as e:
        print(
            f"{file.name}: {e.__class__.__name__}: {str(e)}", file=sys.stderr
        )
        return []

    for symbol in symtab:
        if symbol.size == 0:
            # TODO: Add an option to also index 0-size symbols
            continue
        ret.append(SymbolIndex.serialize_symbol(symbol))

    if not ret:
        # Add a single document if there are no symbols.  Otherwise,
        # we would always treat it as unindexed.
        ret.append(SymbolIndex.serialize_symbol(Symbol(file, "", "", 0)))

    return ret


def index_binary_directory(directory, index_path) -> IndexingStats:
    """Index the given directory."""
    stats = IndexingStats()

    bindir_path = Path(directory)

    with SymbolIndex(index_path, readonly=False) as index:
        if index.binary_dir() is None:
            index.set_binary_dir(bindir_path)

        mtime = index.mtime()
        existing_files = list(index.all_files())
        bdir = BinaryDirectory(bindir_path, mtime, existing_files)

        changed_files = list(bdir.changed_files())
        deleted_files = list(bdir.deleted_files())

        stats.num_files_changed = len(changed_files)
        stats.num_files_deleted = len(deleted_files)

        with (
            sigint_catcher() as interrupted,
            mp.Pool() as pool,
            index.transaction(),
        ):
            perfile_iterator = zip(
                changed_files,
                pool.imap(_read_and_serialize_symtable, changed_files),
            )

            iterator = tqdm(
                perfile_iterator, unit="file", total=len(changed_files)
            )

            for file in deleted_files:
                index.delete_file(file)

            max_mtime = mtime

            for path, serialized_docs in iterator:
                for doc in serialized_docs:
                    index.add_serialized_document(doc)
                stats.num_files_indexed += 1
                stats.num_symbols_indexed += len(serialized_docs)

                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                if mtime > max_mtime:
                    max_mtime = mtime

                if interrupted():
                    print("Interrupted, exiting", file=sys.stderr)
                    break

        index.set_mtime(max_mtime)

    return stats


def search_index(
    index_path: Path,
    query: str,
    consumer: Callable[[Symbol], None],
    limit: Optional[int] = None,
):
    """Search the given index."""
    # Support wildcard search without specifying the field name
    query = re.sub(
        "name:name:", "name:", re.sub("([^ ]+)[*]", "name:\\1*", query)
    )

    if not query:
        query = "*:*"

    with SymbolIndex(index_path) as index:
        for symbol in index.search(query, limit=limit):
            consumer(symbol)

# The lake interface: what every lake backend must offer, written without any storage library so callers can
# depend on it cheaply. Takes in raw bytes and plain row dicts; gives back the same. Two kinds of data live in a
# lake: raw objects (files kept byte-for-byte, e.g. a filing's HTML) and tables (rows with a fixed set of columns).
# To add a backend: write one class with these methods in its own module and register it in agent_data/platform.py.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from agent_data.engine._shared.errors import LakeError

# The column types a table may use; each backend maps these names onto its own storage types.
ColumnType = Literal["string", "int32", "int64", "bool", "date", "timestamp"]

# A table's columns in order: column name -> column type. Kept as plain data so the interface needs no imports.
TableSchema = Mapping[str, ColumnType]

# The column types I support, used to reject a typo in a schema before anything is written.
COLUMN_TYPES: frozenset[str] = frozenset({"string", "int32", "int64", "bool", "date", "timestamp"})


@runtime_checkable
class LakeStore(Protocol):
    """Raw objects plus tables in one storage location (a local folder, s3://, abfss://)."""

    uri: str

    def put_object(self, path: str, data: bytes) -> None:
        """Save bytes at a path relative to the lake root, replacing any object already there."""

    def get_object(self, path: str) -> bytes:
        """Return the bytes stored at a path; raise LakeError if it doesn't exist."""

    def object_exists(self, path: str) -> bool:
        """True when an object is stored at the path."""

    def list_objects(self, prefix: str) -> list[str]:
        """Every object path under a prefix, sorted, relative to the lake root."""

    def delete_object(self, path: str) -> None:
        """Remove one object; removing a missing object is not an error."""

    def upsert_rows(self, table: str, rows: Sequence[Mapping[str, Any]], schema: TableSchema, key: str) -> int:
        """Insert rows, replacing any row with the same key value; creates the table on first use. Returns rows sent."""

    def read_rows(self, table: str, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """All rows of a table as dicts (only the named columns if given); a table that doesn't exist yet is empty."""

    def check_writable(self) -> None:
        """Write and remove a tiny probe object, raising LakeError with a clear hint if the lake can't be written."""


def validate_relative_path(path: str) -> str:
    # Reject paths that could escape the lake root or address it ambiguously; returns the path unchanged when safe.
    if not isinstance(path, str) or not path.strip():
        raise LakeError("A lake path must be a non-empty string")
    if path.startswith("/") or "\\" in path or "://" in path:
        raise LakeError(f"Lake path {path!r} must be relative to the lake root (no leading '/', '\\' or scheme)")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise LakeError(f"Lake path {path!r} has an empty, '.' or '..' segment")
    return path


def validate_schema(schema: TableSchema, key: str) -> None:
    # Fail before writing when a schema names an unknown type or the key column is missing.
    unknown = {name: kind for name, kind in schema.items() if kind not in COLUMN_TYPES}
    if unknown:
        raise LakeError(f"Unknown column type(s) {unknown}; supported: {sorted(COLUMN_TYPES)}")
    if key not in schema:
        raise LakeError(f"Key column {key!r} is not in the table schema {list(schema)}")

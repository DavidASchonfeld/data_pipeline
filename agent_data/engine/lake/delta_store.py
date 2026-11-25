# The lake backend: Delta tables through delta-rs (`deltalake`) and raw objects through `obstore`, both built on
# the same Rust object-storage layer, so one class serves a local folder, s3://bucket/prefix and abfss://… alike —
# only the URI changes. Takes in a lake URI; gives back a LakeStore (see base.py). No Spark, light on memory.
# Libraries are imported lazily (they live in the optional `lake` extra), and nothing touches storage until first use.

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_data.engine._shared.errors import LakeError, require_extra
from agent_data.engine._shared.logging import get_logger, log_event
from agent_data.engine.lake.base import TableSchema, validate_relative_path, validate_schema

_log = get_logger(__name__)

# URI schemes that mean "cloud object storage"; anything else is treated as a local folder path.
_S3_SCHEMES = ("s3://",)
_AZURE_SCHEMES = ("abfss://", "az://", "azure://")
_GCS_SCHEMES = ("gs://",)
_CLOUD_SCHEMES = _S3_SCHEMES + _AZURE_SCHEMES + _GCS_SCHEMES

# Where the write probe goes; a fixed folder so a leftover probe (crash mid-check) is easy to recognise.
_PROBE_PREFIX = "_probe"

# A plain hint per storage kind, added to permission errors so the fix is obvious from the log.
_ACCESS_HINTS = {
    "s3": "check the EC2 role's lake bucket policy, that pods can reach instance credentials "
          "(launch template IMDS hop limit 2), and AWS_REGION",
    "azure": "check the managed identity / storage account role assignment",
    "gcs": "check the service account's bucket permissions",
    "local": "check the folder exists and is writable by this user",
}


class DeltaLakeStore:
    """LakeStore backed by Delta tables (delta-rs) and raw objects (obstore) under one root URI."""

    # Lets platform code and tests tell a real backend apart from a placeholder, like StubBackend does.
    component = "lake"
    backend = "delta"
    is_stub = False

    def __init__(self, uri: str, *, timeout_seconds: float = 60.0, max_retries: int = 3) -> None:
        # Remember where the lake lives and how patient network calls may be; storage is opened on first use.
        if not isinstance(uri, str) or not uri.strip():
            raise LakeError("The lake URI is empty; set LAKE_URI or choose a profile with a default lake")
        if timeout_seconds <= 0 or max_retries < 0:
            raise LakeError("Lake timeout must be positive and max_retries must be zero or more")
        self.uri = _normalise_uri(uri.strip())
        self.kind = _storage_kind(self.uri)
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._store: Any = None   # the obstore handle, built lazily by _objects()

    def __repr__(self) -> str:
        # Show where the lake is; URIs carry no secrets (credentials come from the environment).
        return f"DeltaLakeStore(uri={self.uri!r})"

    # ── Raw objects ─────────────────────────────────────────────────────────────────────────────────────────

    def put_object(self, path: str, data: bytes) -> None:
        # Save bytes at a lake-relative path; the object store replaces any older copy in one step.
        obs = require_extra("obstore", "lake")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise LakeError(f"put_object needs bytes for {path!r}, got {type(data).__name__}")
        self._run(lambda: obs.put(self._objects(), validate_relative_path(path), bytes(data)), "put", path)

    def get_object(self, path: str) -> bytes:
        # Read an object's bytes back; a missing object is a LakeError naming the path.
        obs = require_extra("obstore", "lake")
        return self._run(lambda: bytes(obs.get(self._objects(), validate_relative_path(path)).bytes()), "get", path)

    def object_exists(self, path: str) -> bool:
        # Ask for the object's metadata only (no download); "not found" means False, other errors still raise.
        obs = require_extra("obstore", "lake")
        try:
            self._run(lambda: obs.head(self._objects(), validate_relative_path(path)), "head", path)
        except LakeError as exc:
            if _is_not_found(exc.__cause__):
                return False
            raise
        return True

    def list_objects(self, prefix: str) -> list[str]:
        # Walk every object under the prefix (the store returns them in pages) and return their paths sorted.
        obs = require_extra("obstore", "lake")
        validate_relative_path(prefix)
        pages = self._run(lambda: list(obs.list(self._objects(), prefix=prefix)), "list", prefix)
        return sorted(meta["path"] for page in pages for meta in page)

    def delete_object(self, path: str) -> None:
        # Remove one object; local and cloud stores both treat "already gone" as success.
        obs = require_extra("obstore", "lake")
        try:
            self._run(lambda: obs.delete(self._objects(), validate_relative_path(path)), "delete", path)
        except LakeError as exc:
            if not _is_not_found(exc.__cause__):
                raise

    # ── Tables ──────────────────────────────────────────────────────────────────────────────────────────────

    def upsert_rows(self, table: str, rows: Sequence[Mapping[str, Any]], schema: TableSchema, key: str) -> int:
        # Merge rows into a Delta table by key (insert new, replace existing), creating the table the first time.
        validate_schema(schema, key)
        if not key.isidentifier():
            raise LakeError(f"Key column {key!r} must be a plain identifier")
        if not rows:
            return 0
        data = _to_arrow(_last_row_per_key(rows, key, table), schema, table)
        table_uri = self._table_uri(table)
        if not self._table_exists(table_uri):
            self._create_table(table_uri, data, table)
        else:
            self._merge(table_uri, data, key, table)
        log_event(_log, "lake_upsert", table=table, rows=data.num_rows)
        return data.num_rows

    def read_rows(self, table: str, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
        # Load a whole table (or some columns) as dicts; tables here are small catalogs, not bulk data.
        deltalake = require_extra("deltalake", "lake")
        table_uri = self._table_uri(table)
        if not self._table_exists(table_uri):
            return []
        cols = list(columns) if columns else None
        return self._run(
            lambda: deltalake.DeltaTable(table_uri, storage_options=self._delta_options())
            .to_pyarrow_table(columns=cols).to_pylist(),
            "read table", table,
        )

    def check_writable(self) -> None:
        # Prove the lake accepts a write, a read and a delete before any real work starts (fails fast on bad access).
        probe = f"{_PROBE_PREFIX}/{uuid.uuid4().hex}.txt"
        payload = b"agent_data lake write probe"
        self.put_object(probe, payload)
        try:
            if self.get_object(probe) != payload:
                raise LakeError(f"Lake write probe at {self.uri} read back different bytes")
        finally:
            self.delete_object(probe)
        log_event(_log, "lake_writable", uri=self.uri)

    # ── Internals ───────────────────────────────────────────────────────────────────────────────────────────

    def _objects(self) -> Any:
        # Open the obstore handle once; cloud stores get explicit timeouts and a bounded retry budget.
        if self._store is None:
            store_mod = require_extra("obstore.store", "lake")
            if self.kind == "local":
                Path(self.uri).mkdir(parents=True, exist_ok=True)
                self._store = store_mod.from_url(Path(self.uri).as_uri())
            else:
                self._store = self._run(lambda: store_mod.from_url(
                    self.uri,
                    client_options={"timeout": timedelta(seconds=self._timeout),
                                    "connect_timeout": timedelta(seconds=min(10.0, self._timeout))},
                    retry_config={"max_retries": self._max_retries,
                                  "retry_timeout": timedelta(seconds=self._timeout * (self._max_retries + 1))},
                ), "open", self.uri)
        return self._store

    def _delta_options(self) -> dict[str, str] | None:
        # delta-rs storage options: timeouts for cloud stores, plus S3 conditional writes so commits are atomic
        # without a DynamoDB lock table (S3 rejects a second writer's commit instead of letting it overwrite).
        if self.kind == "local":
            return None
        options = {"timeout": f"{int(self._timeout)}s", "connect_timeout": f"{int(min(10.0, self._timeout))}s"}
        if self.kind == "s3":
            options["conditional_put"] = "etag"
        return options

    def _table_uri(self, table: str) -> str:
        # A table lives in its own folder under the lake root, e.g. <lake>/bronze/ingestion_log.
        validate_relative_path(table)
        return str(Path(self.uri) / table) if self.kind == "local" else f"{self.uri.rstrip('/')}/{table}"

    def _table_exists(self, table_uri: str) -> bool:
        # True when the folder already holds a Delta transaction log.
        deltalake = require_extra("deltalake", "lake")
        return self._run(lambda: deltalake.DeltaTable.is_deltatable(table_uri, storage_options=self._delta_options()),
                         "check table", table_uri)

    def _create_table(self, table_uri: str, data: Any, table: str) -> None:
        # First write: mode="error" refuses to clobber a table that appeared since the existence check.
        deltalake = require_extra("deltalake", "lake")
        self._run(lambda: deltalake.write_deltalake(table_uri, data, mode="error",
                                                    storage_options=self._delta_options()), "create table", table)

    def _merge(self, table_uri: str, data: Any, key: str, table: str) -> None:
        # MERGE by key: matched rows are replaced, new rows inserted — one atomic Delta commit.
        deltalake = require_extra("deltalake", "lake")
        self._run(lambda: (
            deltalake.DeltaTable(table_uri, storage_options=self._delta_options())
            .merge(source=data, predicate=f"t.{key} = s.{key}", source_alias="s", target_alias="t")
            .when_matched_update_all().when_not_matched_insert_all().execute()
        ), "merge into table", table)

    def _run(self, fn: Any, action: str, target: str) -> Any:
        # Call a storage library and turn any failure into one LakeError with the action, target and an access hint.
        try:
            return fn()
        except LakeError:
            raise
        except Exception as exc:  # storage libraries raise many types (OSError, obstore/delta errors); wrap them all
            hint = f" — {_ACCESS_HINTS[self.kind]}" if _looks_like_access_error(exc) else ""
            log_event(_log, "lake_error", action=action, target=target, error=type(exc).__name__)
            raise LakeError(f"Lake {action} failed for {target!r} at {self.uri}: {exc}{hint}") from exc


def _normalise_uri(uri: str) -> str:
    # Cloud URIs are kept as given (bucket names are case-sensitive); local paths become absolute folder paths.
    if uri.startswith(_CLOUD_SCHEMES):
        return uri.rstrip("/")
    if uri.startswith("file://"):
        uri = uri[len("file://"):]
    if "://" in uri:
        raise LakeError(f"Unsupported lake URI scheme in {uri!r}; use a folder path, s3://, abfss:// or gs://")
    return str(Path(uri).expanduser().resolve())


def _storage_kind(uri: str) -> str:
    # Which family of storage the URI points at; used for options and error hints.
    if uri.startswith(_S3_SCHEMES):
        return "s3"
    if uri.startswith(_AZURE_SCHEMES):
        return "azure"
    if uri.startswith(_GCS_SCHEMES):
        return "gcs"
    return "local"


def _is_not_found(exc: BaseException | None) -> bool:
    # Local stores raise FileNotFoundError and cloud stores obstore's NotFoundError; both mean "no such object".
    return isinstance(exc, FileNotFoundError) or (exc is not None and type(exc).__name__ == "NotFoundError")


def _looks_like_access_error(exc: Exception) -> bool:
    # Credentials or permission problems get the access hint; checked by type name so no import is needed.
    names = {cls.__name__ for cls in type(exc).__mro__}
    return bool(names & {"PermissionError", "PermissionDeniedError", "UnauthenticatedError"}) or \
        any(word in str(exc).lower() for word in ("access denied", "forbidden", "credential", "unauthorized"))


def _last_row_per_key(rows: Sequence[Mapping[str, Any]], key: str, table: str) -> list[Mapping[str, Any]]:
    # A MERGE source must not repeat a key, so keep the last row per key; a missing key value is a caller bug.
    by_key: dict[Any, Mapping[str, Any]] = {}
    for row in rows:
        if row.get(key) is None:
            raise LakeError(f"A row for table {table!r} has no value for key column {key!r}")
        by_key[row[key]] = row
    return list(by_key.values())


def _to_arrow(rows: Sequence[Mapping[str, Any]], schema: TableSchema, table: str) -> Any:
    # Build an Arrow table with exactly the schema's columns and types; extra row fields are a caller bug.
    pa = require_extra("pyarrow", "lake")
    arrow_types = {"string": pa.string(), "int32": pa.int32(), "int64": pa.int64(), "bool": pa.bool_(),
                   "date": pa.date32(), "timestamp": pa.timestamp("us", tz="UTC")}
    extra = {name for row in rows for name in row} - set(schema)
    if extra:
        raise LakeError(f"Rows for table {table!r} have columns not in its schema: {sorted(extra)}")
    arrow_schema = pa.schema([(name, arrow_types[kind]) for name, kind in schema.items()])
    try:
        return pa.Table.from_pylist([dict(row) for row in rows], schema=arrow_schema)
    except (pa.ArrowException, TypeError, ValueError) as exc:
        raise LakeError(f"Rows for table {table!r} don't fit its schema: {exc}") from exc

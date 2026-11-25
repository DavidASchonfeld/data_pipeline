# Tests for the Delta lake backend on a temporary local folder: the real deltalake/obstore libraries run, so
# these prove the same code path the cloud URIs use (only the storage location differs). No network needed.

from __future__ import annotations

import datetime as dt

import pytest

from agent_data.engine._shared.errors import LakeError
from agent_data.engine.lake.base import LakeStore, validate_relative_path
from agent_data.engine.lake.delta_store import DeltaLakeStore, _normalise_uri, _storage_kind

pytestmark = pytest.mark.offline

# A small table schema covering every supported column type.
SCHEMA = {"id": "string", "n": "int64", "year": "int32", "ok": "bool", "day": "date", "at": "timestamp"}
NOW = dt.datetime(2026, 11, 23, 12, 0, tzinfo=dt.timezone.utc)


def _row(i: str, n: int = 1) -> dict:
    # One row with a value for every column type.
    return {"id": i, "n": n, "year": 2024, "ok": True, "day": dt.date(2024, 9, 28), "at": NOW}


@pytest.fixture
def lake(tmp_path) -> DeltaLakeStore:
    # A fresh lake in its own temp folder for each test.
    return DeltaLakeStore(str(tmp_path / "lake"))


def test_store_satisfies_the_lake_interface(lake):
    assert isinstance(lake, LakeStore)
    assert (lake.component, lake.backend, lake.is_stub) == ("lake", "delta", False)


def test_construction_touches_no_storage(tmp_path):
    # Building the object must not create folders (platform tests build stores freely).
    DeltaLakeStore(str(tmp_path / "never"))
    assert not (tmp_path / "never").exists()


def test_objects_round_trip_and_list_sorted(lake):
    lake.put_object("raw/b/two.htm", b"<html>2</html>")
    lake.put_object("raw/a/one.htm", b"<html>1</html>")
    assert lake.get_object("raw/a/one.htm") == b"<html>1</html>"
    assert lake.list_objects("raw") == ["raw/a/one.htm", "raw/b/two.htm"]
    assert lake.object_exists("raw/a/one.htm") is True
    assert lake.object_exists("raw/a/missing.htm") is False


def test_put_replaces_and_delete_is_idempotent(lake):
    lake.put_object("x/y.txt", b"old")
    lake.put_object("x/y.txt", b"new")
    assert lake.get_object("x/y.txt") == b"new"
    lake.delete_object("x/y.txt")
    lake.delete_object("x/y.txt")   # already gone: still fine
    assert lake.object_exists("x/y.txt") is False


def test_missing_object_is_a_lake_error_naming_the_path(lake):
    with pytest.raises(LakeError, match="nope.txt"):
        lake.get_object("nope.txt")


def test_put_rejects_non_bytes(lake):
    with pytest.raises(LakeError, match="needs bytes"):
        lake.put_object("a.txt", "text")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "/abs/path", "../escape", "a/../b", "a//b", "s3://x/y", "a\\b", "./a"])
def test_unsafe_paths_are_rejected(bad):
    with pytest.raises(LakeError):
        validate_relative_path(bad)


def test_upsert_creates_then_merges_by_key(lake):
    assert lake.upsert_rows("bronze/t", [_row("a"), _row("b")], SCHEMA, key="id") == 2
    lake.upsert_rows("bronze/t", [_row("b", n=99), _row("c")], SCHEMA, key="id")
    rows = {r["id"]: r for r in lake.read_rows("bronze/t")}
    assert sorted(rows) == ["a", "b", "c"]
    assert rows["b"]["n"] == 99                       # replaced, not duplicated
    assert rows["a"]["day"] == dt.date(2024, 9, 28)   # dates come back as dates
    assert rows["a"]["at"] == NOW                     # timestamps keep their UTC instant


def test_upsert_twice_with_same_rows_changes_nothing(lake):
    lake.upsert_rows("t", [_row("a")], SCHEMA, key="id")
    lake.upsert_rows("t", [_row("a")], SCHEMA, key="id")
    assert len(lake.read_rows("t")) == 1


def test_duplicate_keys_in_one_batch_keep_the_last(lake):
    lake.upsert_rows("t", [_row("a", n=1), _row("a", n=2)], SCHEMA, key="id")
    assert [r["n"] for r in lake.read_rows("t")] == [2]


def test_read_selected_columns_and_missing_table_is_empty(lake):
    assert lake.read_rows("does/not/exist") == []
    lake.upsert_rows("t", [_row("a")], SCHEMA, key="id")
    assert lake.read_rows("t", columns=["id"]) == [{"id": "a"}]


def test_empty_upsert_writes_nothing(lake, tmp_path):
    assert lake.upsert_rows("t", [], SCHEMA, key="id") == 0
    assert lake.read_rows("t") == []


@pytest.mark.parametrize(
    ("rows", "schema", "key", "message"),
    [
        ([{"id": "a"}], {"id": "strng"}, "id", "Unknown column type"),
        ([{"id": "a"}], {"id": "string"}, "missing", "Key column"),
        ([{"id": None}], {"id": "string"}, "id", "no value for key"),
        ([{"id": "a", "extra": 1}], {"id": "string"}, "id", "not in its schema"),
        ([{"id": "a", "n": "not a number"}], {"id": "string", "n": "int64"}, "id", "don't fit its schema"),
    ],
)
def test_bad_rows_or_schemas_fail_before_writing(lake, rows, schema, key, message):
    with pytest.raises(LakeError, match=message):
        lake.upsert_rows("t", rows, schema, key=key)
    assert lake.read_rows("t") == []


def test_check_writable_leaves_no_probe_behind(lake):
    lake.check_writable()
    assert lake.list_objects("_probe") == []


def test_unwritable_folder_fails_with_access_hint(tmp_path):
    # A lake root that is a file, not a folder, can't be written; the error must say so plainly.
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(LakeError):
        DeltaLakeStore(str(blocker)).check_writable()


@pytest.mark.parametrize(
    ("uri", "kind"),
    [("s3://My-Bucket/lake/", "s3"), ("abfss://lake@acct.dfs.core.windows.net/x", "azure"),
     ("gs://b/lake", "gcs"), ("/tmp/lake", "local")],
)
def test_uri_kinds_and_cloud_case_is_kept(uri, kind):
    normalised = _normalise_uri(uri)
    assert _storage_kind(normalised) == kind
    if kind == "s3":
        assert normalised == "s3://My-Bucket/lake"   # bucket names are case-sensitive; trailing slash dropped


def test_s3_commits_use_conditional_writes_and_timeouts():
    # S3 commits must be atomic without a lock table: delta-rs gets conditional_put=etag (no network here).
    options = DeltaLakeStore("s3://bucket/lake", timeout_seconds=30)._delta_options()
    assert options == {"timeout": "30s", "connect_timeout": "10s", "conditional_put": "etag"}
    assert DeltaLakeStore("abfss://c@a.dfs.core.windows.net/l")._delta_options().get("conditional_put") is None


def test_local_file_uri_and_relative_path_become_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert DeltaLakeStore("./lake").uri == str((tmp_path / "lake").resolve())
    assert DeltaLakeStore(f"file://{tmp_path}/lake").uri == str((tmp_path / "lake").resolve())


@pytest.mark.parametrize("bad", ["", "   ", "ftp://host/x"])
def test_bad_uris_are_config_problems(bad):
    with pytest.raises(LakeError):
        DeltaLakeStore(bad)


def test_cloud_table_uri_joins_with_slash():
    assert DeltaLakeStore("s3://b/lake")._table_uri("bronze/t") == "s3://b/lake/bronze/t"

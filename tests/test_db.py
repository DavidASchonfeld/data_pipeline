"""Tests for dashboard/db.py — _cached_query helper."""

import os
import time
from unittest.mock import MagicMock, patch

import pandas as pd


# ── Import with DB_BACKEND forced to non-snowflake so engine creation is skipped ──
with patch.dict(os.environ, {"DB_BACKEND": "mariadb", "DB_HOST": ""}):
    import db as db_module  # dashboard/db.py


# ── _cached_query — guard path (non-Snowflake) ───────────────────────────────

def test_cached_query_returns_empty_df_when_not_snowflake():
    """_cached_query returns an empty DataFrame with correct columns when DB_BACKEND != snowflake."""
    columns = ["col_a", "col_b"]
    query_fn = MagicMock(return_value=pd.DataFrame({"col_a": [1], "col_b": [2]}))

    with patch.object(db_module, "DB_BACKEND", "mariadb"):
        result = db_module._cached_query("test_key", 60, columns, query_fn)

    assert list(result.columns) == columns  # schema must match the columns arg
    assert len(result) == 0               # guard path returns empty — query_fn never called
    query_fn.assert_not_called()          # verify the guard short-circuits before hitting DB


# ── _cached_query — cache hit ────────────────────────────────────────────────

def test_cached_query_returns_cached_result_on_hit():
    """_cached_query returns the cached DataFrame on the second call without invoking query_fn again."""
    columns = ["x"]
    df = pd.DataFrame({"x": [42]})
    call_count = {"n": 0}

    def query_fn():
        call_count["n"] += 1  # track how many times the real query runs
        return df

    with patch.object(db_module, "DB_BACKEND", "snowflake"):
        # Clear any stale cache entry from a previous test run
        db_module._QUERY_CACHE.pop("hit_test_key", None)

        first  = db_module._cached_query("hit_test_key", 3600, columns, query_fn)
        second = db_module._cached_query("hit_test_key", 3600, columns, query_fn)

    assert call_count["n"] == 1           # query_fn ran exactly once
    assert second.equals(first)           # both calls return the same DataFrame


# ── _cached_query — TTL expiry ───────────────────────────────────────────────

def test_cached_query_re_runs_after_ttl_expires():
    """_cached_query re-runs query_fn after the TTL window has passed."""
    columns = ["y"]
    df = pd.DataFrame({"y": [99]})
    call_count = {"n": 0}

    def query_fn():
        call_count["n"] += 1
        return df

    with patch.object(db_module, "DB_BACKEND", "snowflake"):
        db_module._QUERY_CACHE.pop("ttl_test_key", None)

        # First call — populates cache with TTL = 0 seconds (already expired)
        db_module._cached_query("ttl_test_key", 0, columns, query_fn)
        # Force expiry: manually set the cache entry's timestamp to the past
        cached_df, _ = db_module._QUERY_CACHE["ttl_test_key"]
        db_module._QUERY_CACHE["ttl_test_key"] = (cached_df, time.monotonic() - 1)

        # Second call — should miss the expired cache and re-run query_fn
        db_module._cached_query("ttl_test_key", 3600, columns, query_fn)

    assert call_count["n"] == 2  # called twice: once on miss, once after TTL expiry

"""Offline unit tests for the pgvector connection-pool wrapper — psycopg2 is mocked, no DB is touched."""
import importlib
import sys
from unittest.mock import MagicMock

import pytest


def _load_client():
    # install a fake psycopg2 whose pool returns a controllable pool object, then (re)load the
    # client module so its _pool singleton starts fresh for each test
    fake_pool_obj = MagicMock()
    fake_psycopg2 = MagicMock()
    fake_psycopg2.pool.SimpleConnectionPool.return_value = fake_pool_obj
    sys.modules["psycopg2"] = fake_psycopg2
    sys.modules["psycopg2.pool"] = fake_psycopg2.pool
    import genai.retrieval.pgvector_client as pc
    importlib.reload(pc)  # reset the module-level _pool singleton between tests
    return pc, fake_psycopg2, fake_pool_obj


def test_pool_built_with_fail_fast_timeout():
    # the pool must be created with a short connect_timeout and the 1-2 connection cap
    pc, fake_psycopg2, _ = _load_client()
    pc.get_connection()
    args, kwargs = fake_psycopg2.pool.SimpleConnectionPool.call_args
    assert args[0] == 1 and args[1] == 2  # min/max connections
    assert kwargs["connect_timeout"] == 10
    assert kwargs["port"] == 5432


def test_pool_is_a_reused_singleton():
    # the pool is built once and reused — not recreated on every borrow
    pc, fake_psycopg2, _ = _load_client()
    pc.get_connection()
    pc.get_connection()
    assert fake_psycopg2.pool.SimpleConnectionPool.call_count == 1


def test_connection_context_manager_releases_on_success():
    # a normal `with connection()` returns the borrowed connection to the pool
    pc, _, fake_pool_obj = _load_client()
    sentinel = object()
    fake_pool_obj.getconn.return_value = sentinel
    with pc.connection() as conn:
        assert conn is sentinel
    fake_pool_obj.putconn.assert_called_once_with(sentinel)


def test_connection_context_manager_releases_on_exception():
    # the connection is returned even when the body raises — this is what prevents pool exhaustion
    pc, _, fake_pool_obj = _load_client()
    sentinel = object()
    fake_pool_obj.getconn.return_value = sentinel
    with pytest.raises(ValueError):
        with pc.connection() as conn:
            assert conn is sentinel
            raise ValueError("query blew up")
    fake_pool_obj.putconn.assert_called_once_with(sentinel)

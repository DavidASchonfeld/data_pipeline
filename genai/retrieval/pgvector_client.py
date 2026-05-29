from __future__ import annotations

import contextlib
import logging

# Thin connection-pool wrapper for the pgvector Postgres pod.
#
# A "connection pool" keeps 1-2 open database connections ready to reuse rather than
# opening a fresh connection on every query (which is slow). The cap of 2 connections
# keeps RAM usage minimal on the constrained t3.large node.
#
# PREFERRED usage: `with connection() as conn:` — it always returns the connection to the
# pool, even if the query raises. The bare get_connection()/release_connection() pair is kept
# for back-compat, but a forgotten release_connection() exhausts the 2-connection pool.
#
# WHY deferred imports: psycopg2 is heavy. If it were imported at module top level, every
# DAG file that imports this module would trigger that import when Airflow parses DAGs —
# that memory spike can OOM-kill (crash) the 512 MB DAG-processor pod. Deferring the
# import to inside _get_pool() means it only happens when a task actually calls get_connection().

logger = logging.getLogger(__name__)

_pool = None  # module-level singleton; created on the first get_connection() call, then reused


def _get_pool():
    # psycopg2 is imported here, not at module top, so DAG parsing stays memory-safe
    import psycopg2.pool
    from genai import config

    global _pool
    if _pool is None:
        # min=1 keeps one connection warm; max=2 prevents memory spikes on a constrained node
        _pool = psycopg2.pool.SimpleConnectionPool(
            1,
            2,
            host=config.PGVECTOR_HOST,
            user=config.PGVECTOR_USER,
            password=config.PGVECTOR_PASSWORD,
            dbname=config.PGVECTOR_DB,
            port=5432,
            connect_timeout=10,  # fail fast if the pgvector pod is not reachable
        )
    return _pool


def get_connection():
    # Borrow a connection from the pool — caller MUST call release_connection() afterwards or the pool will exhaust.
    # Prefer the connection() context manager below, which releases automatically.
    return _get_pool().getconn()


def release_connection(conn) -> None:
    # Return the borrowed connection to the pool so the next caller can reuse it
    _get_pool().putconn(conn)


@contextlib.contextmanager
def connection():
    # Borrow a connection and guarantee it returns to the pool, even if the caller's query raises.
    # This is the safe default — it makes pool exhaustion from a forgotten release impossible.
    conn = _get_pool().getconn()
    try:
        yield conn
    finally:
        _get_pool().putconn(conn)

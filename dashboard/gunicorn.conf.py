"""
Gunicorn configuration for the dashboard.

post_fork: runs in each worker process immediately after forking.
SQLAlchemy connection objects inherited from the master are unsafe
to share across OS-process boundaries (file-descriptor aliasing).
dispose() discards the inherited pool so each worker opens its own
fresh connections on demand.

graceful_timeout: how long workers have to finish in-flight requests after
SIGTERM before being force-killed. Must be less than terminationGracePeriodSeconds
in pod-flask.yaml (60 s) so the OS never kills the pod mid-drain.
"""

graceful_timeout = 30  # seconds — workers finish in-flight requests on shutdown; must be < terminationGracePeriodSeconds (60 s)


def post_fork(server, worker):
    from db import DB_ENGINE
    if DB_ENGINE is not None:
        DB_ENGINE.dispose()  # discard master-process connections — workers reconnect on first query

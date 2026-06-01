# The SQL safety layer: the single gate every Snowflake query the agent runs must pass through.
#
# Why an allowlist and not a blocklist of bad words: the LLM (and, via the chat UI, the public) can
# propose arbitrary SQL, so I default to DENY and only permit a plain SELECT against two read-only
# schemas. I parse the query into an abstract syntax tree with sqlglot and inspect the actual node
# types — regex on the raw string would be trivially fooled by comments, casing, or whitespace
# (`/*x*/DROP`), whereas the parser sees the real statement. Values are never interpolated into the
# SQL here: the tools pass %(name)s placeholders + a params dict to the driver, so this layer guards
# the STRUCTURE (SELECT-only, allowed tables) and the driver binding guards the VALUES.

from __future__ import annotations

import re

from genai.agent.errors import SqlSafetyError

# Driver bind placeholders (%(name)s named, or %s positional) are not valid SQL — sqlglot reads a bare
# `%` as the modulo operator and fails to parse. Before parsing I swap each placeholder for a harmless
# numeric literal so the parser can read the query's STRUCTURE; the original placeholder string is what
# actually executes (the driver binds the real values). A literal is valid in any value position, so
# this never changes which statement type or tables the parser sees.
_PLACEHOLDER = re.compile(r"%\([^)]*\)s|%s")

# Only these two Snowflake schemas are readable by the agent. RAW and STAGING are intentionally
# excluded — the agent answers from curated marts, never raw ingest. Kept as a local constant rather
# than importing airflow/dags/shared/ because genai/ is self-contained and must import cleanly under
# the ml-venv without the Airflow DAG modules on sys.path (same choice the runners make).
ALLOWED_DATABASE = "PIPELINE_DB"
ALLOWED_SCHEMAS = {"MARTS", "ANALYTICS"}


def validate_select(sql: str) -> str:
    """Parse `sql`, prove it is a single read-only SELECT against the allowlisted MARTS/ANALYTICS
    tables, and return the re-rendered (normalized) query string that is safe to execute. Raise
    SqlSafetyError on anything else — DDL/DML, stacked statements, unqualified or off-limits tables,
    SELECT ... INTO, or text sqlglot cannot parse. The caller executes the RETURNED string, not the
    raw input, so only the vetted form ever reaches Snowflake."""
    import sqlglot
    from sqlglot import exp

    # Parse a placeholder-free PROBE of the query (see _PLACEHOLDER above) against Snowflake's dialect
    # so its syntax (e.g. DATEADD, qualified names) is understood. All checks below run on the probe;
    # the original `sql` is returned for execution so the driver's bind placeholders survive.
    probe = _PLACEHOLDER.sub("0", sql)
    try:
        statements = sqlglot.parse(probe, read="snowflake")
    except sqlglot.errors.ParseError as exc:
        # Never let a raw sqlglot exception escape — one named error type per layer (project convention).
        raise SqlSafetyError(f"could not parse SQL: {exc}") from exc

    # Exactly one statement — this rejects stacked injections like "SELECT 1; DROP TABLE x".
    real = [s for s in statements if s is not None]
    if len(real) != 1:
        raise SqlSafetyError(f"exactly one statement is allowed, got {len(real)}")
    expr = real[0]

    # Unwrap a leading CTE (WITH ...) or parenthesized subquery to find the underlying statement —
    # a write can't hide behind a WITH because step 3 walks the whole tree anyway, but this lets a
    # legitimate `WITH x AS (...) SELECT ...` through.
    root = expr
    while isinstance(root, (exp.Subquery,)):
        root = root.this
    inner = root.this if isinstance(root, exp.With) else root

    # Top-level must be a SELECT (a WITH whose body is a SELECT counts).
    if not isinstance(inner, exp.Select):
        raise SqlSafetyError(f"only SELECT statements are allowed, got {type(inner).__name__}")

    # SELECT ... INTO writes a new table — reject it even though it starts with SELECT.
    if expr.find(exp.Into) is not None:
        raise SqlSafetyError("SELECT ... INTO is not allowed (it writes a table)")

    # Defense in depth: walk EVERY node and reject if any write/DDL or an unclassified raw command
    # appears anywhere (e.g. buried in a subquery or CTE). exp.Command is sqlglot's catch-all for
    # statements it does not model as a typed node — many DROP/TRUNCATE/USE/GRANT forms land here.
    # Resolve the class names defensively: sqlglot renames a few node types across versions (e.g.
    # TruncateTable), so I skip any that this installed version doesn't expose rather than crash —
    # exp.Command still catches anything that slips through.
    _forbidden_names = [
        "Insert", "Update", "Delete", "Merge", "Create", "Drop",
        "Alter", "TruncateTable", "Command", "Into",
    ]
    forbidden = tuple(getattr(exp, name) for name in _forbidden_names if hasattr(exp, name))
    for node in expr.walk():
        if isinstance(node, forbidden):
            raise SqlSafetyError(f"statement contains a forbidden operation: {type(node).__name__}")

    # Names defined by a WITH clause (CTEs) are query-local aliases, not real tables — collect them so
    # a reference to one isn't mistaken for an unqualified table below.
    cte_names = {cte.alias_or_name.upper() for cte in expr.find_all(exp.CTE)}

    # Every referenced table must be fully qualified and point at an allowlisted schema. An
    # unqualified name (no schema) is rejected so a query can never default into RAW/STAGING.
    tables = [t for t in expr.find_all(exp.Table) if t.name.upper() not in cte_names]
    if not tables:
        raise SqlSafetyError("query references no allowlisted table")
    for table in tables:
        schema = (table.db or "").upper()
        catalog = (table.catalog or "").upper()
        if not schema:
            raise SqlSafetyError(f"table '{table.name}' must be schema-qualified (e.g. MARTS.<table>)")
        if schema not in ALLOWED_SCHEMAS:
            raise SqlSafetyError(
                f"schema '{schema}' is not allowed — only {sorted(ALLOWED_SCHEMAS)} are readable"
            )
        if catalog and catalog != ALLOWED_DATABASE:
            raise SqlSafetyError(f"database '{catalog}' is not allowed — only {ALLOWED_DATABASE}")

    # Return the ORIGINAL query (placeholders intact) so the driver can bind values — only the probe
    # was re-rendered for parsing. Strip surrounding whitespace and a single trailing semicolon; any
    # second statement would already have been rejected by the one-statement check above.
    return sql.strip().rstrip(";").strip()

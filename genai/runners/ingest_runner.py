# Standalone RAG ingest runner — runs under /opt/ml-venv (sentence-transformers + psycopg2 + snowflake
# available), invoked by airflow/dags/genai_dags/dag_sec_rag_ingest.py as a subprocess:
#
#   cd /opt/airflow && /opt/ml-venv/bin/python -m genai.runners.ingest_runner --source filings
#   cd /opt/airflow && /opt/ml-venv/bin/python -m genai.runners.ingest_runner --source weather
#
# It pulls source rows from Snowflake, chunks them, embeds only the chunks whose text changed since
# last run, upserts them into the pgvector `chunks` table, and deletes chunks whose source row is gone.
# The last stdout line is a single JSON summary the DAG parses — mirroring the extract_runner pattern.
#
# WHY a subprocess: the embedding model + psycopg2 live in /opt/ml-venv, separate from Airflow's venv,
# and the model must never load inside the scheduler process (it would risk OOM). Running here keeps
# the heavy work isolated. WHY Snowflake connection logic is duplicated (not imported from shared/):
# this script runs under a different venv/sys.path — genai/ stays self-contained (same as extract_runner.py).

from __future__ import annotations

import argparse
import json
import logging
import os

# Logs go to stderr (the Airflow task captures both streams); the JSON summary is the LAST stdout line.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ingest_runner")

# Snowflake source tables (mirror analytics_bootstrap.sql / weather_summaries_table.sql).
_FCT_FILING_SECTIONS = "PIPELINE_DB.ANALYTICS.FCT_FILING_SECTIONS"
_FCT_WEATHER_SUMMARIES = "PIPELINE_DB.MARTS.FCT_WEATHER_SUMMARIES"


# ── Snowflake connection (direct connector — no Airflow hook; matches extract_runner.py) ──


def _load_private_key_der() -> bytes:
    # Read the RSA private key file and return DER bytes — the format snowflake-connector wants.
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    key_path = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
    with open(key_path, "rb") as f:
        p_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_snowflake_conn():
    # Open a Snowflake connection using env vars + RSA key-pair auth (no MFA prompt for the service account).
    import snowflake.connector

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=_load_private_key_der(),
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ.get("SNOWFLAKE_ROLE", "PIPELINE_ROLE"),
    )


# ── Source reads ──────────────────────────────────────────────────────────────


def _read_filing_sections(conn) -> list[dict]:
    # Every stored 10-K section becomes the corpus for the sec_filings namespace.
    cur = conn.cursor()
    cur.execute(f"SELECT ticker, filing_date, section, section_text FROM {_FCT_FILING_SECTIONS}")
    return [{"ticker": t, "filing_date": d, "section": s, "section_text": txt} for t, d, s, txt in cur.fetchall()]


def _read_weather_summaries(conn) -> list[dict]:
    # Every weekly city summary becomes one chunk in the weather namespace.
    cur = conn.cursor()
    cur.execute(f"SELECT city, week_start, summary_text FROM {_FCT_WEATHER_SUMMARIES}")
    return [{"city": c, "week_start": w, "summary_text": txt} for c, w, txt in cur.fetchall()]


# ── Pipeline orchestration ───────────────────────────────────────────────────


def run_pipeline(source: str) -> dict:
    """Read → chunk → embed-changed → upsert → delete-orphans for one source. Returns the JSON summary."""
    from genai.config import GENAI_EMBED_BATCH_SIZE
    from genai.embedding import get_embedder
    from genai.retrieval import ingest, pgvector_client

    embedder = get_embedder()

    # Build the chunks for this source from its Snowflake rows.
    conn = get_snowflake_conn()
    try:
        if source == "filings":
            namespace = ingest.NAMESPACE_FILINGS
            chunks = ingest.make_filing_chunks(_read_filing_sections(conn), embedder)
        else:
            namespace = ingest.NAMESPACE_WEATHER
            chunks = ingest.make_weather_chunks(_read_weather_summaries(conn), embedder)
    finally:
        conn.close()

    logger.info("source=%s namespace=%s built %d chunks", source, namespace, len(chunks))

    # Upsert + orphan-delete in ONE pgvector transaction so a mid-write failure leaves the index intact.
    seen_ids = [c.external_id for c in chunks]
    with pgvector_client.connection() as pg:
        try:
            stats = ingest.upsert_chunks(pg, namespace, chunks, embedder, GENAI_EMBED_BATCH_SIZE)
            orphans_deleted = ingest.delete_orphans(pg, namespace, seen_ids)
            pg.commit()
        except Exception:
            pg.rollback()  # never leave the index half-written
            raise

    logger.info("source=%s upserted=%d skipped (hash unchanged)=%d orphans_deleted=%d",
                source, stats["upserted"], stats["skipped"], orphans_deleted)
    return {
        "source": source,
        "namespace": namespace,
        "chunks_total": len(chunks),
        "chunks_upserted": stats["upserted"],
        "chunks_skipped": stats["skipped"],
        "orphans_deleted": orphans_deleted,
        "errors": [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunk + embed Snowflake source rows into the pgvector chunks table")
    parser.add_argument("--source", required=True, choices=["filings", "weather"], help="Which source to ingest")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    summary = run_pipeline(args.source)
    print(json.dumps(summary))  # last line of stdout — the DAG parses this as the task result

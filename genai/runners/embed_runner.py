# Standalone embedding runner — runs under /opt/ml-venv (sentence-transformers available), invoked
# as a subprocess by the EPIC 7 ingest DAG:
#
#   cd /opt/airflow && /opt/ml-venv/bin/python -m genai.runners.embed_runner
#
# It is a STREAMING FILTER, not the argparse + last-line-summary shape the other runners use:
#   stdin  — one JSON object per line: {"id": <any>, "text": "..."}
#   stdout — one JSON object per line: {"id": <same>, "vector": [384 floats]}, in input order
#   stderr — logging only (model load, counts, skipped malformed lines)
# Streaming this way keeps memory bounded: a big filing is read one line at a time and only one batch
# of vectors is ever held in memory, so peak RSS stays well under the EPIC's 800 MB ceiling.
#
# WHY a subprocess: the embedding model lives in /opt/ml-venv, separate from Airflow's venv, and the
# model exits with the process so the long-running scheduler pod never carries it in-process.

from __future__ import annotations

import json
import logging
import sys
from typing import TextIO

# Logs go to stderr (the Airflow task captures both streams); stdout carries ONLY the {id, vector} lines.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("embed_runner")

# How many lines to embed per model call. 32 matches EPIC 7's batch size and bounds the rows + vectors
# held in memory at once — the whole input is never materialized.
_BATCH_SIZE = 32


def _flush(embedder, buffer: list[tuple], out: TextIO) -> None:
    # Embed one buffered batch and write a JSON line per row, preserving input order, then clear the buffer.
    if not buffer:
        return
    vectors = embedder.embed_batch([text for _, text in buffer])
    for (rid, _), vector in zip(buffer, vectors):
        out.write(json.dumps({"id": rid, "vector": vector}))
        out.write("\n")
    out.flush()  # stream results out so a downstream reader sees them incrementally, not all at the end
    buffer.clear()


def run(stdin: TextIO, stdout: TextIO) -> int:
    # Read {id, text} lines, embed them in bounded batches, write {id, vector} lines. Returns an exit code.
    from genai.embedding import get_embedder  # deferred — only load the model layer when actually running

    embedder = get_embedder()
    buffer: list[tuple] = []
    n_in = 0
    n_bad = 0

    for line in stdin:  # iterating the stream reads one line at a time — never loads the whole input
        line = line.strip()
        if not line:
            continue  # ignore blank lines so trailing newlines don't error
        try:
            obj = json.loads(line)
            rid = obj["id"]
            text = obj["text"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            # A single malformed line is skipped (logged), not fatal — one bad row shouldn't kill the run.
            n_bad += 1
            logger.warning("skipping malformed input line: %s", exc)
            continue
        buffer.append((rid, text))
        n_in += 1
        if len(buffer) >= _BATCH_SIZE:
            _flush(embedder, buffer, stdout)

    _flush(embedder, buffer, stdout)  # final partial batch (also handles the single-line case)
    logger.info("embedded %d lines (%d skipped) with %s", n_in, n_bad, embedder.model_id)
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.stdin, sys.stdout))

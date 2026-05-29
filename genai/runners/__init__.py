# genai.runners — standalone subprocess scripts (LLM extraction, future embedding/ingest) run under
# /opt/ml-venv so heavy SDKs never load inside the Airflow scheduler process. Each runner is a CLI
# that prints a single JSON summary on its last stdout line, mirroring airflow/dags/anomaly_detector.py.

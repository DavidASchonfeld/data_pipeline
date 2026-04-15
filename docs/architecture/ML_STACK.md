# Machine Learning Stack: scikit-learn & MLflow

Modern data pipelines don't just move data from point A to point B — they also need to ensure that data is clean, reliable, and ready for downstream analysis and machine learning systems. This project integrates two industry-standard tools to meet that bar: **scikit-learn** for anomaly detection and **MLflow** for model tracking.

---

## scikit-learn

**What it is:** scikit-learn is Python's most widely-used toolkit for applying statistical and machine learning algorithms to data. Think of it as a well-tested library of pattern-recognition tools that any data or ML practitioner can reach for.

**How it's used here:** The pipeline pulls public financial data from the SEC for hundreds of companies and tracks how their revenue and net income change year over year. scikit-learn's **IsolationForest** algorithm is used to automatically flag companies whose numbers look unusual compared to the broader dataset.

IsolationForest is an *unsupervised* algorithm — meaning it doesn't need anyone to label examples of "good" or "bad" data in advance. It learns what normal looks like on its own, then identifies outliers that don't fit the pattern. For example, if every other company in the dataset saw revenue grow roughly 5–10% in a given year, but one company jumped 300%, IsolationForest would flag that as an anomaly worth investigating.

Each company-year combination receives two outputs:
- **`is_anomaly`** — a yes/no flag
- **`anomaly_score`** — a numeric score (lower = more unusual)

These results are written to a dedicated Snowflake table (`FCT_ANOMALIES`) and surfaced in the dashboard.

---

## MLflow

**What it is:** MLflow is an open-source platform for tracking and managing machine learning experiments. It acts as a logbook: every time the anomaly detector runs, MLflow records exactly what happened.

**What it tracks:** Each run logs the model's settings (e.g. what percentage of the data it expected to be anomalous), the results (how many anomalies were found out of how many companies), and a saved copy of the trained model itself — including the input/output schema so the model can be validated and reused.

**Why this matters:** MLflow makes every model run fully auditable. If a result looks surprising — say, the number of flagged anomalies suddenly spikes — you can pull up the exact run in MLflow, see every parameter, and reproduce the result from scratch. Nothing is a black box.

MLflow is deployed as an in-cluster service running inside the same Kubernetes environment as Airflow, so it integrates seamlessly with the rest of the pipeline.

---

## How They Fit Into the Pipeline

```
SEC EDGAR API
     ↓
  Kafka (message queue)
     ↓
Snowflake RAW (raw financial data)
     ↓
  dbt (clean and transform)
     ↓
Snowflake MARTS (FCT_COMPANY_FINANCIALS)
     ↓
IsolationForest — scikit-learn  ←→  MLflow (logs every run)
     ↓
Snowflake ANALYTICS (FCT_ANOMALIES)
     ↓
  Dashboard
```

The anomaly detection step runs automatically at the end of each daily batch, after the data has been validated by dbt. Results appear in the dashboard as a scatter plot and detail table, with anomalous companies highlighted in red.

---

## Why These Tools

**scikit-learn** is the industry standard for this class of problem. IsolationForest is specifically designed for unsupervised anomaly detection — it works well even when you don't have labeled training data, which is the case here.

**MLflow** reflects a broader shift in what data engineering involves. A modern data pipeline doesn't just deliver raw data — it feeds machine learning systems, and those systems need to be reproducible, auditable, and observable just like the data itself. MLflow is the standard tool for that responsibility. It means that every model prediction in this project is traceable: you can always answer *what model ran, with what settings, and what it found*.

Both tools run in an isolated Python environment (`/opt/ml-venv`) baked into the Docker image, keeping their dependencies cleanly separated from Airflow's own packages.

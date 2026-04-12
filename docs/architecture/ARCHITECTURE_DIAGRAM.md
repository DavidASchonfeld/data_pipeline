# Architecture Diagram

System overview of the data pipeline. See [README.md](../../README.md) for full context.

```mermaid
flowchart LR
    subgraph APIs["External APIs"]
        SEC["SEC EDGAR\n(stocks, daily)"]
        OMeteo["Open-Meteo\n(weather, hourly)"]
    end

    subgraph KF["Apache Kafka · K3S"]
        K1["stocks-financials-raw"]
        K2["weather-hourly-raw"]
    end

    subgraph SF["Snowflake · PIPELINE_DB"]
        RAW["RAW\n(raw ingest)"]
        STAG["STAGING\n(dbt views)"]
        MARTS["MARTS\n(dbt tables)"]
        ANALYTICS["ANALYTICS\nFCT_ANOMALIES"]
        RAW --> STAG --> MARTS --> ANALYTICS
    end

    SEC --> K1
    OMeteo --> K2
    K1 & K2 --> RAW
    MARTS & ANALYTICS --> DSH["Flask + Dash\nDashboard :32147"]
    ANALYTICS --> MLF["MLflow\n(experiment tracking)"]
    Airflow["Apache Airflow\n(LocalExecutor · K3S · EC2)"] -.->|orchestrates| APIs
    Airflow -.->|orchestrates| KF
    Airflow -.->|orchestrates| SF
    Airflow -.->|staleness monitor| Slack["Slack\n(60-min cooldown)"]
```

## Component Notes

| Component | Detail |
|-----------|--------|
| **Apache Airflow** | LocalExecutor on K3S; 5 DAGs (stocks producer/consumer, weather producer/consumer, staleness monitor) |
| **Apache Kafka 4.0** | KRaft mode (no ZooKeeper), plain StatefulSet; 2 topics, 48h/100MB retention each |
| **Snowflake** | PIPELINE_DB; RAW written by consumer DAGs; STAGING/MARTS built by dbt; FCT_ANOMALIES written by anomaly_detector.py |
| **Flask + Dash** | NodePort 32147; 1-hour query cache; pre-warmed at container startup |
| **MLflow** | Tracks every anomaly detection run — parameters, metrics, model artifact |
| **Slack** | Fired by staleness monitor DAG when either pipeline hasn't run recently; 60-min cooldown prevents alert floods |

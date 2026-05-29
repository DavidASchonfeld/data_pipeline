# Third-Party Notices & Credits

This file lists every third-party tool, library, container image, dataset, and external API this project depends on, together with its license. It is the project's **attribution and proof-of-right-to-use record**: it documents that each component is used under a license that permits it.

**How this project uses third-party software:** by depending on it (installing it, or running an unmodified upstream container image) and calling its public interfaces. No third-party source code is copied into this repository.

**Keeping it current:** add a row whenever a dependency is added, removed, or version-bumped. Full upstream license texts (which most permissive licenses require you to retain) belong in the `licenses/` folder next to this file.

> **Disclaimer:** This inventory is maintained on a best-effort basis and is not legal advice. Licenses can change between versions — verify each one against the version actually installed before relying on it commercially. The items under "Licenses needing attention" are starting points for that review, not conclusions.

---

## Python — Dashboard (`dashboard/requirements.txt`)

| Library | Version | License | Project |
|---|---|---|---|
| Flask | 2.3.3 | BSD-3-Clause | https://flask.palletsprojects.com |
| gunicorn | 22.0.0 | MIT | https://gunicorn.org |
| python-dotenv | 1.0.1 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| dash | 2.17.1 | MIT | https://dash.plotly.com |
| plotly | 5.22.0 | MIT | https://plotly.com/python |
| pandas | 2.2.2 | BSD-3-Clause | https://pandas.pydata.org |
| SQLAlchemy | 2.0.30 | MIT | https://www.sqlalchemy.org |
| PyMySQL | 1.1.1 | MIT | https://github.com/PyMySQL/PyMySQL |
| snowflake-connector-python | 3.10.1 | Apache-2.0 | https://github.com/snowflakedb/snowflake-connector-python |
| snowflake-sqlalchemy | 1.6.1 | Apache-2.0 | https://github.com/snowflakedb/snowflake-sqlalchemy |
| flask-limiter | 3.5.0 | MIT | https://github.com/alisaifee/flask-limiter |
| flask-talisman | 1.1.0 | Apache-2.0 | https://github.com/GoogleCloudPlatform/flask-talisman |
| flask-cors | 4.0.1 | MIT | https://github.com/corydolphin/flask-cors |
| requests | ≥2.31.0 | Apache-2.0 | https://requests.readthedocs.io |

## Python — Airflow image base + runtime (`airflow/docker/Dockerfile`)

| Library | Version | License | Project |
|---|---|---|---|
| Apache Airflow | 3.1.8 | Apache-2.0 | https://airflow.apache.org |
| apache-airflow-providers-* | — | Apache-2.0 | https://airflow.apache.org |
| kafka-python | — | Apache-2.0 | https://github.com/dpkp/kafka-python |
| python-dotenv | — | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| openai (Python SDK) | — | Apache-2.0 | https://github.com/openai/openai-python |
| anthropic (Python SDK) | ≥0.50.0 | MIT | https://github.com/anthropics/anthropic-sdk-python |
| pytest | — | MIT | https://pytest.org |
| chardet | ≥3.0.2,<6 | **LGPL-2.1** | https://github.com/chardet/chardet |

## Python — dbt environment (`/opt/dbt-venv`)

| Library | Version | License | Project |
|---|---|---|---|
| dbt-core | 1.11.8 | Apache-2.0 | https://github.com/dbt-labs/dbt-core |
| dbt-snowflake | 1.11.0 | Apache-2.0 | https://github.com/dbt-labs/dbt-snowflake |
| openlineage-dbt | — | Apache-2.0 | https://github.com/OpenLineage/OpenLineage |

## Python — ML / GenAI environment (`/opt/ml-venv`)

| Library | Version | License | Project |
|---|---|---|---|
| MLflow | 2.15.1 | Apache-2.0 | https://mlflow.org |
| scikit-learn | 1.5.2 | BSD-3-Clause | https://scikit-learn.org |
| pandas | 2.2.2 | BSD-3-Clause | https://pandas.pydata.org |
| setuptools | <75 | MIT | https://github.com/pypa/setuptools |
| PyTorch (torch, CPU build) | — | BSD-3-Clause | https://pytorch.org |
| sentence-transformers | — | Apache-2.0 | https://www.sbert.net |
| anthropic (Python SDK) | ≥0.50.0 | MIT | https://github.com/anthropics/anthropic-sdk-python |
| psycopg2-binary | — | **LGPL-3.0** | https://www.psycopg.org |
| pgvector (Python client) | — | MIT | https://github.com/pgvector/pgvector-python |
| rank-bm25 | — | Apache-2.0 | https://github.com/dorianbrown/rank_bm25 |
| beautifulsoup4 | — | MIT | https://www.crummy.com/software/BeautifulSoup/ |
| numpy | <2 | BSD-3-Clause | https://numpy.org |

### Embedding model (downloaded at runtime)
| Asset | License | Source |
|---|---|---|
| sentence-transformers/all-MiniLM-L6-v2 | Apache-2.0 | https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2 |

## Container images & infrastructure

| Component | Version | License | Project |
|---|---|---|---|
| pgvector/pgvector (image) | pg16 | PostgreSQL License | https://github.com/pgvector/pgvector |
| PostgreSQL (inside the image) | 16 | PostgreSQL License | https://www.postgresql.org |
| apache/airflow (image) | 3.1.8 | Apache-2.0 | https://airflow.apache.org |
| busybox (init-container image) | — | **GPL-2.0** (unmodified upstream image) | https://busybox.net |
| Apache Kafka | — | Apache-2.0 | https://kafka.apache.org |
| K3s | — | Apache-2.0 | https://k3s.io |
| Docker Engine | — | Apache-2.0 | https://www.docker.com |
| Terraform | — | **BUSL-1.1** (Business Source License) | https://www.terraform.io |

## External data sources & APIs (services, used under their terms)

| Service | Terms / data license | Link |
|---|---|---|
| SEC EDGAR | U.S. government public-domain data; requires a descriptive User-Agent and fair-access rate limits | https://www.sec.gov/os/accessing-edgar-data |
| Open-Meteo | Data under **CC BY 4.0**; free for non-commercial use, commercial use needs a subscription — attribution required | https://open-meteo.com/en/terms |
| Anthropic API | Commercial API under Anthropic's Commercial Terms of Service | https://www.anthropic.com/legal/commercial-terms |
| OpenAI API | Commercial API under OpenAI's Terms of Use | https://openai.com/policies/terms-of-use |
| Slack (incoming webhooks) | Used under Slack API Terms of Service | https://slack.com/terms-of-service/api |

## Planned / not yet added (see ADR 0005)

| Library | License | Project | Notes |
|---|---|---|---|
| unstructured (open-source core) | Apache-2.0 | https://github.com/Unstructured-IO/unstructured | Multi-format document parsing. The hosted **Platform** is a separate paid product — library only unless a plan is purchased. |
| llama-index | MIT | https://github.com/run-llama/llama_index | Optional document readers / connectors. |

---

## Licenses needing attention

Most dependencies above are permissive (MIT / BSD / Apache-2.0) and only require retaining the copyright notice and license text (drop them in `licenses/`). A few warrant a closer look:

- **chardet — LGPL-2.1** and **psycopg2 — LGPL-3.0.** LGPL is "weak copyleft." Using these as unmodified, separately-installed libraries (the normal pip case) is generally fine and does not impose copyleft on this project's own code. The obligations would only bite if these libraries were modified or statically bundled into a redistributed binary. Note: `psycopg2-binary` ships precompiled; an alternative is the equally-LGPL `psycopg2` source build — license is the same either way.
- **busybox — GPL-2.0.** Only used as an unmodified upstream init-container image to fix directory permissions. Running an unmodified image is permitted; do not modify and redistribute it. If GPL presence is undesirable even at the image level, swap it for an `alpine` or `postgres` image to run the same `chmod`/`chown`.
- **Terraform — BUSL-1.1.** The Business Source License permits normal use (provisioning your own infrastructure) but forbids offering Terraform itself as a competing commercial service. If a fully open license is preferred, **OpenTofu** (MPL-2.0) is a drop-in fork.
- **Open-Meteo — CC BY 4.0.** The data requires **attribution** and is free for **non-commercial** use; commercial use needs a paid plan. Make sure the dashboard credits Open-Meteo and that the usage tier matches the intended use.

# Third-Party Notices & Credits

This file lists every third-party tool, library, container image, dataset, and external API this project depends on, together with its license. It is the project's **attribution and proof-of-right-to-use record**: it documents that each component is used under a license that permits it.

**How this project uses third-party software:** by depending on it (installing it, or running an unmodified upstream container image) and calling its public interfaces. No third-party source code is copied into this repository.

**Keeping it current:** add a row whenever a dependency is added, removed, or version-bumped. Full upstream license texts (which most permissive licenses require you to retain) live in the `licenses/` folder next to this file: one canonical body per license family (`MIT.txt`, `Apache-2.0.txt`, …), a `COPYRIGHT-NOTICES.txt` manifest mapping each component below to its verbatim upstream copyright line, and the `NOTICE` files for the Apache-2.0 projects that ship one. See `licenses/README.md` for the layout.

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
| snowflake-connector-python | 3.10.1 (dashboard) / 3.18.0 (ml-venv) | Apache-2.0 | https://github.com/snowflakedb/snowflake-connector-python |
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
| MLflow | 3.13.0 | Apache-2.0 | https://mlflow.org |
| scikit-learn | 1.5.2 | BSD-3-Clause | https://scikit-learn.org |
| pandas | 2.2.2 | BSD-3-Clause | https://pandas.pydata.org |
| setuptools | <75 | MIT | https://github.com/pypa/setuptools |
| PyTorch (torch, CPU build) | — | BSD-3-Clause | https://pytorch.org |
| sentence-transformers | — | Apache-2.0 | https://www.sbert.net |
| anthropic (Python SDK) | ≥0.50.0 | MIT | https://github.com/anthropics/anthropic-sdk-python |
| openai (Python SDK) | — | Apache-2.0 | https://github.com/openai/openai-python |
| psycopg2-binary | — | **LGPL-3.0** | https://www.psycopg.org |
| pgvector (Python client) | — | MIT | https://github.com/pgvector/pgvector-python |
| rank-bm25 | — | Apache-2.0 | https://github.com/dorianbrown/rank_bm25 |
| sqlglot | — | MIT | https://github.com/tobymao/sqlglot |
| beautifulsoup4 | — | MIT | https://www.crummy.com/software/BeautifulSoup/ |
| pydantic | — | MIT | https://github.com/pydantic/pydantic |
| python-dotenv | — | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| numpy | <2 | BSD-3-Clause | https://numpy.org |

### Embedding model (downloaded at runtime)
| Asset | License | Source |
|---|---|---|
| sentence-transformers/all-MiniLM-L6-v2 | Apache-2.0 | https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2 |

## Python — agent_data package (`pyproject.toml`)

Versions are the ones I license-checked on 2026-11-23; `pyproject.toml` holds the allowed ranges. Extras install only when requested (e.g. `pip install -e ".[lake]"`).

| Library | Version | License | Used by | Project |
|---|---|---|---|---|
| pydantic | 2.14.4 | MIT | core | https://github.com/pydantic/pydantic |
| pydantic-settings | 2.15.0 | MIT | core | https://github.com/pydantic/pydantic-settings |
| typing-inspection | 0.4.5 | MIT | core (via pydantic) | https://github.com/pydantic/typing-inspection |
| typing_extensions | 4.16.0 | PSF-2.0 | core (via pydantic) | https://github.com/python/typing_extensions |
| deltalake (delta-rs) | 1.6.6 | Apache-2.0 | `[lake]` | https://github.com/delta-io/delta-rs |
| arro3-core | 0.8.3 | MIT OR Apache-2.0 | `[lake]` (via deltalake) | https://github.com/kylebarron/arro3 |
| snowflake-connector-python | 4.8.0 | Apache-2.0 | `[snowflake]` | https://github.com/snowflakedb/snowflake-connector-python |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | `[snowflake]`, `[dev]` | https://github.com/pyca/cryptography |
| FastAPI | 0.162.5 | MIT | `[api]` | https://github.com/fastapi/fastapi |
| Starlette | 1.7.0 | BSD-3-Clause | `[api]` (via FastAPI) | https://github.com/encode/starlette |
| annotated-doc | 0.0.6 | MIT | `[api]` (via FastAPI) | https://github.com/fastapi/annotated-doc |
| AnyIO | 4.16.0 | MIT | `[api]` (via Starlette) | https://github.com/agronholm/anyio |
| Uvicorn | 0.54.0 | BSD-3-Clause | `[api]` | https://github.com/encode/uvicorn |
| h11 | 0.16.0 | MIT | `[api]` (via Uvicorn) | https://github.com/python-hyper/h11 |
| Click | 8.5.1 | BSD-3-Clause | `[api]` (via Uvicorn) | https://github.com/pallets/click |
| pytest | 9.2.0 | MIT | `[dev]` | https://pytest.org |
| import-linter | 2.15 | BSD-2-Clause | `[dev]` | https://github.com/seddonym/import-linter |
| grimp | 3.18 | BSD-2-Clause | `[dev]` (via import-linter) | https://github.com/seddonym/grimp |
| ruff | 0.17.6 | MIT | `[dev]` | https://github.com/astral-sh/ruff |
| setuptools | ≥68 | MIT | build backend for `pip install -e .` | https://github.com/pypa/setuptools |
| certifi | 2026.11.20 | **MPL-2.0** | transitive (via requests / snowflake-connector) | https://github.com/certifi/python-certifi |

### Local development stack (`docker-compose.yml`)
| Component | Version | License | Project |
|---|---|---|---|
| pgvector/pgvector (image) | pg16 | PostgreSQL License | https://github.com/pgvector/pgvector |
| python (official Docker image, base of the local API image) | 3.12-slim | PSF-2.0 (Python); Debian packages under their own licenses (unmodified upstream image) | https://hub.docker.com/_/python |

## Container images & infrastructure

| Component | Version | License | Project |
|---|---|---|---|
| pgvector/pgvector (image) | pg16 | PostgreSQL License | https://github.com/pgvector/pgvector |
| PostgreSQL (inside the image) | 16 | PostgreSQL License | https://www.postgresql.org |
| apache/airflow (image) | 3.1.8 | Apache-2.0 | https://airflow.apache.org |
| Apache Airflow Helm chart (fetched by `scripts/deploy/airflow_pods.sh`, SHA-512 verified) | 1.20.0 | Apache-2.0 | https://archive.apache.org/dist/airflow/helm-chart/1.20.0/ |
| busybox (init-container image) | — | **GPL-2.0** (unmodified upstream image) | https://busybox.net |
| Apache Kafka | — | Apache-2.0 | https://kafka.apache.org |
| K3s | — | Apache-2.0 | https://k3s.io |
| Docker Engine | — | Apache-2.0 | https://www.docker.com |
| Terraform | — | **BUSL-1.1** (Business Source License) | https://www.terraform.io |
| Amazon SSM Agent (pre-installed on the Ubuntu AMI) | — | Apache-2.0 | https://github.com/aws/amazon-ssm-agent |
| AWS Session Manager plugin (operator's laptop, optional) | — | Apache-2.0 | https://github.com/aws/session-manager-plugin |

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
- **certifi — MPL-2.0** (and two small MPL-2.0 Rust crates, `colored` and `option-ext`, compiled into the `deltalake` wheel). MPL is file-level weak copyleft: obligations apply only to modified MPL files that are distributed. I use these unmodified via pip and do not redistribute them, so nothing applies to this project's code.
- **busybox — GPL-2.0.** Only used as an unmodified upstream init-container image to fix directory permissions. Running an unmodified image is permitted; do not modify and redistribute it. If GPL presence is undesirable even at the image level, swap it for an `alpine` or `postgres` image to run the same `chmod`/`chown`.
- **Terraform — BUSL-1.1.** The Business Source License permits normal use (provisioning your own infrastructure) but forbids offering Terraform itself as a competing commercial service. If a fully open license is preferred, **OpenTofu** (MPL-2.0) is a drop-in fork.
- **Open-Meteo — CC BY 4.0.** The data requires **attribution** and is free for **non-commercial** use; commercial use needs a paid plan. Make sure the dashboard credits Open-Meteo and that the usage tier matches the intended use.

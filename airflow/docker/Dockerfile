# must match the tag in values.yaml exactly
FROM apache/airflow:3.1.8

# root needed to install python3-venv and create venv dirs under /opt (root-owned)
USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# /opt/dbt-venv is isolated from Airflow's Python — dbt pins SQLAlchemy < 2.0, Airflow 3.x requires >= 2.0
# baking into the image ensures the venv survives pod restarts (runtime installs disappear on restart)
# chown here so the airflow user can run pip in the next USER block without being root
RUN python3 -m venv /opt/dbt-venv \
    && chown -R airflow: /opt/dbt-venv

# drop to airflow user to run pip — avoids "Running pip as root" warning and follows least-privilege best practice
USER airflow

# dbt-core + dbt-snowflake pinned to 1.11.x — transitive deps (dbt-adapters, dbt-common) resolved by pip
RUN /opt/dbt-venv/bin/pip install --upgrade pip \
    && /opt/dbt-venv/bin/pip install \
         dbt-core==1.11.8 \
         dbt-snowflake==1.11.0 \
         openlineage-dbt

# back to root briefly to create the ml-venv dir under /opt and set up pip cache ownership
USER root

# /opt/ml-venv: isolated venv for MLflow + ML libs — avoids conflicts with Airflow's pinned deps
# setuptools pinned <75: mlflow 2.15.1 requires it at runtime; install verified via importlib.metadata
# chown here so the airflow user can run pip in the next USER block without being root
RUN python3 -m venv /opt/ml-venv \
    && chown -R airflow: /opt/ml-venv

# OpenLineage config — console transport writes events to stdout (visible in Airflow task logs)
# To use Marquez instead, replace "console" with: type: http\n  config:\n    url: http://marquez:5000
COPY --chown=airflow:root openlineage.yml /opt/openlineage.yml

# Give the airflow user write access to the pip cache created by earlier root RUN steps
RUN mkdir -p /tmp/.cache/pip && chown -R airflow: /tmp/.cache/pip

# drop to airflow user for all remaining pip installs — non-root from here on
USER airflow

# requests/chardet pinned here to match airflow_pods.sh fallback install — fast-path verify uses these baked versions
RUN /opt/ml-venv/bin/pip install --upgrade pip \
    && /opt/ml-venv/bin/pip install \
         mlflow==2.15.1 \
         scikit-learn==1.5.2 \
         pandas==2.2.2 \
         snowflake-connector-python==3.10.1 \
         "setuptools<75" \
         "requests>=2.32.0" \
         "chardet>=3.0.2,<6" \
    && /opt/ml-venv/bin/python -c "import sklearn, mlflow, snowflake.connector, pandas; import importlib.metadata; importlib.metadata.version('setuptools'); print('ml-venv imports OK')"

# requests warns when chardet ≥6 is installed (check: chardet must be <6); also upgrade requests itself
RUN pip install "requests>=2.32.3" "chardet>=3.0.2,<6"

# runtime packages baked in so init containers don't run pip at startup (which caused 5-7 min delays and 600s timeouts)
# NOTE: do NOT add apache-airflow-providers-snowflake or snowflake-connector-python here —
# apache/airflow:3.1.8 already bundles them; downgrading them breaks the startup probe (~7 min download)
RUN pip install \
    pymysql \
    "requests>=2.32.3" \
    "apache-airflow-providers-common-compat>=1.5.0" \
    kafka-python \
    python-dotenv

"""Shared dbt helpers — BashOperator factory used by both consumer DAGs."""


def make_dbt_operator(task_id: str, command: str, select_tag: str):
    """Return a BashOperator for dbt run or test with standard env vars and OpenLineage.

    Eliminates the copy-pasted BashOperator blocks in dag_stocks_consumer.py and
    dag_weather_consumer.py — the only differences are task_id, command, and select_tag.

    task_id:    Airflow task identifier (e.g. 'dbt_run', 'dbt_test')
    command:    dbt sub-command ('run' or 'test')
    select_tag: dbt node selector tag (e.g. 'stocks', 'weather')
    """
    from airflow.providers.standard.operators.bash import BashOperator  # deferred: avoids parse-time import
    return BashOperator(
        task_id=task_id,
        bash_command=(
            "mkdir -p /tmp/dbt_target /tmp/dbt_logs && "   # ensure artifact dirs exist before dbt-ol runs
            "PATH=/opt/dbt-venv/bin:$PATH "                # ensures dbt-ol's internal Popen(['dbt']) resolves correctly
            "DBT_PROFILES_DIR=/dbt "                       # profiles.yml mounted from K8s secret at /dbt
            "OPENLINEAGE_CONFIG=/opt/openlineage.yml "     # emits lineage events via console transport
            "DBT_TARGET_PATH=/tmp/dbt_target "             # dbt-ol uses this for artifact writing and post-run reading
            "DBT_LOG_PATH=/tmp/dbt_logs "                  # dbt 1.8+: write log file to /tmp, not project-dir
            f"/opt/dbt-venv/bin/dbt-ol {command} "        # dbt-ol wraps dbt and emits OpenLineage events after completion
            f"--select tag:{select_tag} "                  # only run models with this tag — skips unrelated models
            "--project-dir /opt/airflow/dags/dbt "
            "--no-use-colors"                              # cleaner logs in Airflow UI
        ),
    )

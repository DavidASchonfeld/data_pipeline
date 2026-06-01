"""Offline unit tests for the SQL safety layer — the gate that proves a query is a read-only SELECT
against the allowlisted MARTS/ANALYTICS schemas before it ever reaches Snowflake. No network."""
import pytest

from genai.agent.errors import SqlSafetyError
from genai.agent.sql_safety import validate_select

pytestmark = pytest.mark.offline


# ── Allowed: plain SELECTs against the two readable schemas ──────────────────────


@pytest.mark.parametrize("sql", [
    "SELECT summary_text FROM PIPELINE_DB.MARTS.FCT_WEATHER_SUMMARIES WHERE city = %(city)s LIMIT 1",
    "SELECT ticker, fiscal_year FROM PIPELINE_DB.ANALYTICS.FCT_ANOMALIES WHERE ticker = %(ticker)s",
    "SELECT revenue FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS",
    # a CTE whose body reads an allowlisted table — the CTE alias 'r' must not be mistaken for a table
    "WITH r AS (SELECT * FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS) SELECT * FROM r",
    # a join across both allowed schemas
    "SELECT a.ticker FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS a "
    "JOIN PIPELINE_DB.ANALYTICS.FCT_ANOMALIES b ON a.ticker = b.ticker",
])
def test_allows_readonly_selects(sql):
    out = validate_select(sql)
    # The ORIGINAL placeholders survive so the driver can bind values (the probe is only for parsing).
    assert "FROM" in out.upper()
    if "%(" in sql:
        assert "%(" in out


def test_returns_original_placeholders_and_strips_trailing_semicolon():
    sql = "SELECT summary_text FROM PIPELINE_DB.MARTS.FCT_WEATHER_SUMMARIES WHERE city = %(city)s ;"
    out = validate_select(sql)
    assert out.endswith("%(city)s")     # trailing semicolon + whitespace stripped, placeholder intact


# ── Refused: the acceptance case and every other non-SELECT or off-limits target ──


def test_rejects_drop_table():
    # EPIC 9 acceptance check 3 at the unit level: "drop the financials table" must be refused here.
    with pytest.raises(SqlSafetyError):
        validate_select("DROP TABLE PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS")


def test_rejects_natural_language_drop():
    with pytest.raises(SqlSafetyError):
        validate_select("drop the financials table")


@pytest.mark.parametrize("sql", [
    "DELETE FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS",
    "UPDATE PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS SET revenue = 0",
    "INSERT INTO PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS VALUES (1)",
    "MERGE INTO PIPELINE_DB.MARTS.X USING PIPELINE_DB.MARTS.Y ON X.id = Y.id "
    "WHEN MATCHED THEN UPDATE SET X.v = Y.v",
    "TRUNCATE TABLE PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS",
    "CREATE TABLE PIPELINE_DB.MARTS.X (id INT)",
    "ALTER TABLE PIPELINE_DB.MARTS.X ADD COLUMN y INT",
])
def test_rejects_dml_and_ddl(sql):
    with pytest.raises(SqlSafetyError):
        validate_select(sql)


def test_rejects_stacked_statements():
    # A second statement smuggled in after a SELECT — the classic injection — is refused.
    with pytest.raises(SqlSafetyError):
        validate_select("SELECT 1 FROM PIPELINE_DB.MARTS.X; DROP TABLE PIPELINE_DB.MARTS.X")


def test_rejects_select_into():
    with pytest.raises(SqlSafetyError):
        validate_select("SELECT ticker INTO newtbl FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM PIPELINE_DB.RAW.WEATHER_HOURLY",          # RAW is off-limits
    "SELECT * FROM PIPELINE_DB.STAGING.STG_WEATHER_HOURLY",  # STAGING is off-limits
])
def test_rejects_non_allowlisted_schema(sql):
    with pytest.raises(SqlSafetyError):
        validate_select(sql)


def test_rejects_unqualified_table():
    # No schema → could default into RAW/STAGING, so it must be refused.
    with pytest.raises(SqlSafetyError):
        validate_select("SELECT x FROM FCT_ANOMALIES")


def test_rejects_other_database():
    with pytest.raises(SqlSafetyError):
        validate_select("SELECT x FROM OTHER_DB.MARTS.FCT_ANOMALIES")


def test_rejects_write_hidden_in_subquery():
    # A RAW reference nested in a subquery must still be caught (the walk inspects the whole tree).
    with pytest.raises(SqlSafetyError):
        validate_select(
            "SELECT * FROM PIPELINE_DB.MARTS.X WHERE id IN (SELECT id FROM PIPELINE_DB.RAW.Y)"
        )


def test_unparseable_input_raises_sql_safety_error():
    # Garbage must surface as our named type, never a raw sqlglot ParseError.
    with pytest.raises(SqlSafetyError):
        validate_select("SELECT FROM WHERE )(")

# Weather DAG Snowflake Write Failure (2026-04-07)

Date: April 7, 2026

## The Problem

The weather pipeline was successfully fetching data from the Open-Meteo API (168 rows every hour) and successfully writing it to MariaDB (the local database). But when it tried to write the same data to Snowflake (the cloud data warehouse), it failed silently — the data never appeared in Snowflake's tables.

- API call: 168 rows extracted (working)
- MariaDB write: 168 rows inserted (working)
- Snowflake write: 0 rows inserted (silently failed)

---

## The Root Cause: A Timestamp Mismatch

### How the Table Schema Got Broken

The Snowflake table was supposed to have `TIMESTAMP_NTZ` columns, but when Python's `write_pandas()` auto-created the table, it incorrectly inferred the type as `NUMBER(38,0)` — storing timestamps as Unix epoch seconds (the number of seconds since January 1, 1970) instead of readable dates.

### Why the Deduplication Logic Broke

The weather pipeline checks "have I already written this data?" before writing to prevent duplicates. The code:

1. Queried Snowflake for existing times — got epoch numbers: `[1712476800, 1712480400, ...]`
2. Converted them to strings: `["1712476800", "1712480400", ...]`
3. Got API times as ISO strings: `["2026-04-07T00:00", "2026-04-07T01:00", ...]`
4. Compared: `"2026-04-07T00:00"` vs `"1712476800"` — they never match

The code was comparing human-readable date strings with epoch numbers as strings. Different formats, so the comparison always failed.

### The Type-Check Order Bug

A second issue: `snowflake_client.py` checked `isinstance(val, str)` before `isinstance(val, datetime)`. Datetime objects got treated as strings, bypassing the epoch conversion code.

---

## The Fix

### Fix 1: Convert timestamps to epoch seconds before comparing

```python
# Convert API times to epoch seconds first
df_times_epoch = pd.to_datetime(df["time"]).astype(int) // 10**9
# Now comparing: 1712476800 vs 1712476800 — matches correctly
```

### Fix 2: Check datetime types before string types

```python
# Correct order:
elif isinstance(val, (datetime, pd.Timestamp)):  # Check this FIRST
    epoch_seconds = int(val.timestamp())
elif isinstance(val, str):  # Check this SECOND
    ...
```

---

## Verification

After deploying the fix:
```
Snowflake has 0 existing timestamps
Snowflake dedup: 0 existing, 168 new rows
Loaded 168 rows into Snowflake WEATHER_HOURLY
```

168 rows successfully written to Snowflake.

---

## Key Takeaways

1. **Timestamps have many formats** — date strings, Unix epoch numbers, and Python datetime objects are all the same moment in time but look completely different. Code that compares them must convert to the same format first.
2. **Type checking order matters** — checking `isinstance(val, str)` before `isinstance(val, datetime)` means datetime objects get treated as strings.
3. **Workarounds have ripple effects** — using epoch numbers instead of TIMESTAMP columns requires updating all code that touches that data.
4. **Silent failures are the hardest to debug** — the write didn't raise an error; it wrote 0 rows without complaint. Always check the logs.

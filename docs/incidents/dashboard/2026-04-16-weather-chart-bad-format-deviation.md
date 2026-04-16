# Incident: "encountered bad format: +.1f" on Weather page

**Date:** 2026-04-16  
**Component:** Dashboard — Weather anomaly scatter chart  
**Severity:** Warning only — chart rendered, hover tooltip may have shown garbled or missing deviation value

---

## What Happened

When navigating back and forth between the Stocks and Weather pages, the browser console showed:

```
WARN: encountered bad format: "+.1f"
```

The warning came from Plotly's internal charting library while trying to display the "Diff from Avg" value in the hover tooltip on the temperature scatter chart.

---

## Plain-English Explanation

Hover tooltips in Plotly charts can include number formatting instructions — for example, "show this number to 1 decimal place." One of those instructions was `+.1f`, which means "show the number with a + or − sign and 1 decimal place" (e.g. `+2.3` or `−0.8`).

Python understands this format perfectly. However, the version of Plotly used in this project uses a JavaScript library called d3 to do the final number rendering inside the browser. That library does not accept the `+` sign modifier written this way, and printed a warning about it.

The result: the "Diff from Avg" line in the hover tooltip likely showed the raw number without a sign, or possibly a blank value.

---

## Fix Applied

**File:** `dashboard/weather_charts.py` (line ~142)

Instead of asking Plotly/d3 to format the number with a sign, the deviation value is now formatted in Python before it is sent to the chart. Python has no trouble with `+.1f`, so the value arrives pre-formatted (e.g. the string `"+2.3"`) and Plotly just displays it as-is — no formatting instruction needed in the browser.

**Before:**
```python
customdata = custom[["city_name", "city_mean", "deviation"]].values
# hovertemplate: "Diff from Avg: %{customdata[2]:+.1f}°F"  ← d3 rejects :+.1f
```

**After:**
```python
custom["deviation_str"] = custom["deviation"].apply(lambda v: f"{v:+.1f}")
customdata = custom[["city_name", "city_mean", "deviation_str"]].values
# hovertemplate: "Diff from Avg: %{customdata[2]}°F"  ← plain string, no format spec
```

The tooltip display is identical — values like `+2.3°F` or `−0.8°F` — with no browser warnings.

---

## When to Investigate Further

If the "Diff from Avg" row in the weather chart tooltip shows a blank or raw unformatted number, check `dashboard/weather_charts.py` around the `deviation_str` line and verify the `.apply(lambda v: f"{v:+.1f}")` call is still present.

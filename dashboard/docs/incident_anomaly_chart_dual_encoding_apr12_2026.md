# Anomaly Chart: Dual Encoding Enhancement — April 12, 2026

## What Changed

The Anomaly Detection scatterplot in the dashboard was updated so that each company's points are visually distinct while still making it easy to tell normal points from anomaly points.

## What the Old Chart Did

The old chart used two traces: all normal points were drawn as blue circles, and all anomaly points were drawn as red X markers. This meant you could tell normal from anomaly at a glance, but you couldn't tell which company any given point belonged to — every company shared the same colors.

## What the New Chart Does

The new chart uses **dual encoding**, where two independent visual properties each carry different information at the same time:

- **Color** tells you which company a point belongs to. Each ticker (AAPL, MSFT, GOOGL, etc.) gets a unique color from Plotly's standard 10-color qualitative palette. Colors are assigned alphabetically so they stay consistent across page refreshes.
- **Shape** tells you whether a point is normal or anomalous. A circle means normal. An X means anomaly. This works regardless of company color.

The legend at the right of the chart shows a colored entry for each company, followed by two grey shape-key entries ("Normal (○)" and "Anomaly (✕)") that document the shape convention.

Hovering over any point now shows the company ticker, fiscal year, anomaly score (3 decimal places), and both YoY growth percentages.

## Why This Is an Improvement

Previously, if AAPL had an anomalous year, you had to cross-reference the table below the chart to find out which company the red X belonged to. Now, AAPL's anomaly point is clearly AAPL-colored with an X shape, making company-level patterns visible directly in the scatter view.

## Files Changed

- `dashboard/charts.py` — only file modified

## What Was Added

Two private helper functions were added before `build_anomaly_scatter`:

- `_build_color_map(tickers)` — assigns one distinct color per ticker
- `_anomaly_symbols(is_anomaly_col)` — returns a per-point list of "circle" or "x" symbols

`build_anomaly_scatter` was refactored from a 2-trace approach (split by anomaly flag) to an N-trace approach (one trace per company, with per-point symbol lists).

## No Downstream Impact

`callbacks.py`, `db.py`, and `app.py` were not changed. The function signature of `build_anomaly_scatter(df)` is unchanged. The empty-DataFrame guard and `update_layout` block are unchanged.

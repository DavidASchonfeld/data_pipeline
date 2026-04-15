# Feature: Interactive Anomaly Table — Legend Sync & Column Sorting

**Date:** April 15, 2026
**Dashboards affected:** Stocks (`/dashboard/`) and Weather (`/weather/`)

---

## What changed

The anomaly section on both dashboards previously had two separate, independent pieces: a chart and a table below it. They didn't talk to each other at all — hiding a company or city in the chart had no effect on the table.

Two interactive features were added to make the two pieces work together and make the table easier to navigate.

---

## Feature 1 — The chart and table stay in sync

**What it does:**
When you click a company name (on the Stocks page) or a city name (on the Weather page) in the chart's legend — the coloured labels running along the right or top of the chart — that company or city is hidden from the chart. Now, the table below also immediately hides rows for that company or city.

Clicking the same name again brings those rows back in both the chart and the table.

**Why it was added:**
Before this change, a viewer could narrow the chart down to one company but the table still showed every company. This made it hard to focus on a specific company or city when the table was long.

**How to use it:**
- Click any coloured name in the chart legend once to hide it. The table updates instantly.
- Click the same name again to bring it back.
- You can hide as many or as few as you like. If all names are hidden, the table shows a message letting you know.

A short tip explaining this is displayed directly above the table on both dashboards.

---

## Feature 2 — Sort the table by any column

**What it does:**
You can now sort the anomaly table by any column — for example, sort by Severity to see the most extreme readings at the top, or sort alphabetically by City to group readings together.

**How to use it:**
- **Double-click** any column header (the grey label at the top of a column, like "City" or "Score") to sort the table by that column. An arrow (▲) appears next to the header to show it is the active sort.
- **Click the same header once** to flip the sort order from lowest-to-highest (▲) to highest-to-lowest (▼), or back again.
- **Double-click a different column header** to switch the sort to that column instead. Only one column can be the sort column at a time.
- **Double-click the active column header** to clear the sort and return to the default order.

**Default order (no column selected):**
- Stocks table: anomalies appear first, then normal rows — the same order the pipeline writes them.
- Weather table: most extreme temperature anomalies appear first.

---

## Technical summary (for developers)

| File | Change |
|------|--------|
| `dashboard/anomaly_table.py` | **New.** Shared column definitions, row builders, and visibility-parsing logic used by both dashboards. |
| `dashboard/assets/anomaly_sort.js` | **New.** Browser-side JavaScript that measures click timing to distinguish a single click (toggle direction) from a double-click (select column). Auto-loaded by Dash. |
| `dashboard/app.py` | Added invisible `dcc.Store` components for data and sort state; replaced the old static table `div` with a static-header + dynamic-body table; added tip text above each anomaly table. |
| `dashboard/callbacks.py` | Split the old single anomaly callback into two: one that loads data and renders the chart, and a second that listens to the data store, the chart's legend clicks (`restyleData`), and the sort state to re-render table rows. Added clientside callbacks for the sort state. |
| `dashboard/charts.py` | Removed `build_anomaly_table` — replaced by `anomaly_table.py`. |
| `dashboard/weather_charts.py` | Removed `build_weather_anomaly_table` and `_severity_label` — both moved to `anomaly_table.py`. |
| `dashboard/assets/theme.css` | Added CSS for `.sortable-header`, `.sorted`, and `.sort-indicator`. |

**How the sync works:**
Plotly fires a `restyleData` event whenever the user toggles a legend entry. Dash captures this event as a callback input. The callback also reads the current figure state to check which traces are marked `"legendonly"` (hidden), then filters the table rows to match.

**How the sort works:**
A JavaScript file runs in the browser and tracks the time between header clicks. If two clicks on the same non-active column happen within 400 milliseconds, it counts as a double-click and selects that column as the sort. A single click on the already-active column just flips the direction. The sort state is stored in a hidden Dash component and triggers a server-side callback that re-renders the table rows in the new order.

---

## Feature 3 — Color circle column in the anomaly table

**Date added:** April 15, 2026

**What it does:**
A very small circle now appears as the first column in each anomaly table row. The circle is filled with the same color used for that company or city in the chart just above the table — matching the colored dots already visible in the chart's legend on the right-hand side.

**Why it was added:**
When the table has many rows, it can be hard to mentally match a row back to a specific colored dot on the chart. The circle in the first column makes that connection immediate — your eye can follow the color from the chart legend straight across to the matching table row without having to read and compare names.

**What it looks like:**
- The column has no header text — just a blank space above the circles.
- The circles are small (about the same size as the colored dots in the chart legend) and use the same exact color.
- The column cannot be sorted and has no click behavior. Clicking it does nothing.
- The rest of the row text stays the same standard white color — only the dot is colored, keeping the table easy to read.

**Technical summary (for developers):**

| File | Change |
|------|--------|
| `dashboard/anomaly_table.py` | Added `extract_color_map(figure, skip_names)` to read hex colors from the live Plotly figure dict; added `_color_dot_cell(color)` helper that returns a narrow `<td>` containing a 10×10 px filled circle; updated both row-builder functions to accept an optional `color_map` dict and prepend the dot cell to every row; incremented `colSpan` on empty-state rows to cover the new column. |
| `dashboard/callbacks.py` | Imported `extract_color_map`; both table-rendering callbacks now call it after reading the figure state and pass the resulting dict to the row builders. |
| `dashboard/app.py` | Added one leading `html.Th("", className="color-dot-header")` to each anomaly table header — no `id`, `n_clicks`, or `sortable-header` class, so the JavaScript sort system ignores it completely. |
| `dashboard/assets/theme.css` | Added `.color-dot-header` (narrow fixed width, `cursor: default`, suppressed hover style) and `.color-dot-cell` (matching width, centered, vertically aligned) CSS rules. |

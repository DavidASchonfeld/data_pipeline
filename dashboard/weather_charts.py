import numpy as np  # z-score computation for weather anomaly detection
import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pc  # qualitative palette for per-city distinct colors
from dash import html

from theme import CHART_THEME as _CHART_THEME  # shared dark theme — single source of truth, no longer duplicated here
# ─────────────────────────────────────────────────────────────────────────────


def build_temperature_fig(df: pd.DataFrame, city: str = "") -> go.Figure:
    """Line chart of hourly temperature (°F) over the last 7 days.

    Single trace keeps the chart readable; blue matches the existing dashboard palette.
    city: optional city name shown in the chart title; omit to use the generic title.
    """
    # Guard: return an annotated empty figure if no data has arrived from the pipeline yet
    if df.empty:
        fig = go.Figure()
        # Apply dark theme so the empty state doesn't show a jarring white panel
        fig.update_layout(**_CHART_THEME)
        fig.add_annotation(
            text="No weather data yet", showarrow=False,
            font={"size": 14, "color": "#8892a4"},  # cool gray — muted placeholder text
        )
        return fig

    fig = go.Figure(data=[go.Scatter(
        x=df["observation_time"],       # hourly timestamps on the x-axis
        y=df["temperature_f"],          # temperature in Fahrenheit on the y-axis
        mode="lines",                   # continuous line (no dots) — cleaner for dense hourly data
        name="Temperature (°F)",
        line={"color": "#3b82f6", "width": 2.5},  # cornflower blue — increased width for dark-bg legibility
        hovertemplate="%{x}<br>%{y:.1f}°F<extra></extra>",  # clean tooltip showing time + temp
    )])
    # Apply shared dark theme then add chart-specific title/axis labels
    fig.update_layout(
        **_CHART_THEME,
        title=f"{city} — 7-Day Hourly Temperature (°F)" if city else "7-Day Hourly Temperature (°F)",  # include city name when provided
        xaxis_title="Date / Time",      # label tells the viewer the x-axis is time
        yaxis_title="Temperature (°F)", # label clarifies the unit
        hovermode="x unified",          # unified hover shows all traces at the same x position
    )
    return fig


def build_weather_stats_table(df: pd.DataFrame):
    """Summary stats table: current temp, 24-hour min/max, and location metadata.

    Filters to the last 24 hours for min/max so the values stay relevant to today.
    Returns html.P placeholder if no data is available yet.
    """
    # Guard: show a friendly message instead of an empty or broken table
    if df.empty:
        return html.P("No weather data yet — run the pipeline to generate results.")

    # Latest row gives current temperature and location metadata
    latest = df.iloc[-1]
    current_temp = f"{latest['temperature_f']:.1f}°F"  # one decimal place is enough precision
    # Show city name if available; fall back to raw coordinates for backward compatibility
    if "city_name" in df.columns and pd.notna(latest.get("city_name", None)):
        location = str(latest["city_name"])
    else:
        location = f"{latest['latitude']:.1f}°N, {latest['longitude']:.1f}°E"  # human-readable coordinates
    elevation = f"{latest['elevation']:.0f} m"          # elevation in whole meters
    timezone = str(latest["timezone"])                   # IANA timezone string from Open-Meteo

    # Filter to last 24 hours to compute today's min and max temperature
    cutoff = df["observation_time"].max() - pd.Timedelta(hours=24)  # 24-hour window relative to latest timestamp
    last_24h = df[df["observation_time"] >= cutoff]                  # slice to the 24-hour window
    temp_min = f"{last_24h['temperature_f'].min():.1f}°F"           # coldest reading in the window
    temp_max = f"{last_24h['temperature_f'].max():.1f}°F"           # warmest reading in the window

    # ── Header ────────────────────────────────────────────────────────────────
    header_cols = ["Current Temp", "24h Min", "24h Max", "Location", "Elevation", "Timezone"]
    header = html.Thead(html.Tr([
        html.Th(c) for c in header_cols  # CSS .dash-table th handles all header cell styling
    ]))

    # ── Single data row ───────────────────────────────────────────────────────
    cells = [current_temp, temp_min, temp_max, location, elevation, timezone]
    body = html.Tbody(html.Tr([
        html.Td(v) for v in cells  # CSS .dash-table td handles padding, borders, and font
    ]))

    return html.Table(
        className="dash-table",  # CSS class provides dark surface, borders, and layout
        children=[header, body],
    )

# ── Weather anomaly detection ─────────────────────────────────────────────────

def _build_city_color_map(cities: list) -> dict:
    """Maps each city to a distinct qualitative color — same pattern as stocks' _build_color_map."""
    palette = pc.qualitative.Plotly
    return {c: palette[i % len(palette)] for i, c in enumerate(cities)}


def compute_weather_anomalies(df: pd.DataFrame, z_threshold: float = 2.0) -> pd.DataFrame:
    """Per-city z-score anomaly detection on hourly temperature readings.

    For each city, flags any reading more than z_threshold standard deviations
    from that city's 7-day mean temperature.  Runs entirely in-memory on the
    cached weather DataFrame — no extra Snowflake queries.
    """
    # Guard: return df with extra columns pre-filled so callers never see KeyError
    anomaly_cols = ["city_mean", "city_std", "deviation", "z_score", "is_anomaly"]
    if df.empty:
        for col in anomaly_cols:
            df[col] = pd.NA
        return df

    df = df.copy()  # avoid mutating the cached DataFrame

    # Per-city mean and std broadcast back to original shape via transform
    df["city_mean"] = df.groupby("city_name")["temperature_f"].transform("mean")
    df["city_std"]  = df.groupby("city_name")["temperature_f"].transform("std")

    df["deviation"] = df["temperature_f"] - df["city_mean"]
    # Avoid division by zero when a city has constant temperature over the window
    df["z_score"] = np.where(
        df["city_std"] > 0,
        np.abs(df["deviation"]) / df["city_std"],
        0.0,
    )
    df["is_anomaly"] = df["z_score"] > z_threshold  # True = flagged as unusual for this city

    # Sort by city then time for deterministic trace ordering in the scatter chart
    df = df.sort_values(["city_name", "observation_time"])
    return df


def build_weather_anomaly_scatter(df: pd.DataFrame) -> go.Figure:
    """Scatter of Time vs Temperature (°F), dual-encoded: color = city, shape = anomaly.

    Circle = normal reading, x = unusually high or low reading for that city.
    Shows all cities together so viewers can spot outliers at a glance.
    """
    # Guard: empty figure with dark theme before any data arrives
    if df.empty or "is_anomaly" not in df.columns:
        fig = go.Figure()
        fig.update_layout(**_CHART_THEME)
        fig.add_annotation(
            text="No data yet", showarrow=False,
            font={"size": 14, "color": "#8892a4"},
        )
        return fig

    cities = sorted(df["city_name"].unique())  # sorted for deterministic color assignment
    color_map = _build_city_color_map(cities)

    fig = go.Figure()

    # One trace per city — color encodes identity, per-point symbol encodes anomaly status
    for city in cities:
        sub = df[df["city_name"] == city].sort_values("is_anomaly")  # normals first so legend swatch shows circle
        symbols = ["x" if v else "circle" for v in sub["is_anomaly"]]  # shape encodes anomaly flag
        # Pack hover fields: city name, city avg, diff from avg — keeps tooltip non-technical
        custom = sub[["city_name", "city_mean", "deviation"]].copy()
        fig.add_trace(go.Scatter(
            x=sub["observation_time"],    # time on X — intuitive for all viewers
            y=sub["temperature_f"],       # temperature on Y — concrete physical quantity
            mode="markers",
            name=city,
            marker={
                "color":  color_map[city],
                "size":   9,
                "symbol": symbols,
            },
            customdata=custom[["city_name", "city_mean", "deviation"]].values,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Time: %{x|%Y-%m-%d %H:%M}<br>"
                "Temp: %{y:.1f}°F<br>"
                "City Avg: %{customdata[1]:.1f}°F<br>"
                "Diff from Avg: %{customdata[2]:+.1f}°F"
                "<extra></extra>"  # suppresses the trace-name box
            ),
        ))

    # Two invisible shape-key entries so the legend documents circle=normal / x=anomaly
    for label, sym in [("Normal (○)", "circle"), ("Anomaly (✕)", "x")]:
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode="markers",
            name=label,
            marker={"symbol": sym, "size": 9, "color": "#8892a4"},  # cool gray — neutral legend swatch
            showlegend=True,
        ))

    fig.update_layout(
        **_CHART_THEME,
        title="Temperature Readings — Unusual Values Highlighted",
        xaxis_title="Date / Time",
        yaxis_title="Temperature (°F)",
    )
    return fig


# _severity_label and build_weather_anomaly_table removed — both moved to anomaly_table.py
# and wired via the render_weather_anomaly_table callback in callbacks.py
# ─────────────────────────────────────────────────────────────────────────────

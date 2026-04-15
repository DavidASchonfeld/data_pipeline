"""Centralized design tokens — single source of truth for all dashboard colors.

These constants are consumed by:
- dashboard/theme.py (Plotly chart theme)
- terraform/lambda/wake.py (loading page inline styles — must be kept in sync manually)

When changing a color here, also update the matching CSS custom property in
dashboard/assets/theme.css so the Plotly charts and CSS components stay consistent.
"""

# ── Background layers (darkest to lightest) ──────────────────────────────────
BG_BASE      = "#0f1117"  # near-black — outermost page canvas
BG_SURFACE   = "#1a1d27"  # dark navy  — cards, chart panels
BG_ELEVATED  = "#222638"  # dark slate-blue — table headers, nav bar
BG_HOVER     = "#2a2f45"  # muted indigo — row hover, active states

# ── Text ──────────────────────────────────────────────────────────────────────
TEXT_PRIMARY   = "#e2e8f0"  # soft white   — body and heading text
TEXT_SECONDARY = "#8892a4"  # cool gray    — labels, subtitles
TEXT_MUTED     = "#4a5568"  # charcoal     — placeholders, disabled

# ── Accent colors ─────────────────────────────────────────────────────────────
ACCENT_BLUE        = "#3b82f6"  # cornflower blue — links, primary action
ACCENT_BLUE_BRIGHT = "#60a5fa"  # sky blue        — button hover, active
ACCENT_GREEN       = "#10b981"  # emerald green   — Net Income bars

# ── Borders ───────────────────────────────────────────────────────────────────
BORDER       = "#2d3348"  # dark steel — table cell dividers
BORDER_LIGHT = "#374160"  # slate      — nav/header borders

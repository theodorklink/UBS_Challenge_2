"""
Bloomberg three-panel HTML renderer — Stage 6.

Renders a single self-contained HTML file with three panels:
  Left   — Sieyuan + China T&D basket
  Middle — Siemens Energy + Global Power basket (with Grid Tech segment row)
  Right  — Cross-basket spread + commentary

True black background, amber accents, JetBrains Mono. No external deps
beyond Google Fonts CDN.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..comparables.models import CompCell, CompRow, CompTable
from ..spread.cross_basket import (
    DirectPairLine,
    PolicyEntry,
    RelativePositioning,
    ResetSensitivity,
    SpreadAnalysis,
)
from ..summary.commentary import Commentary


# ---------------------------------------------------------------------------
# Formatters


def fmt_num(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:,.{decimals}f}"


def fmt_pct(v: Optional[float], decimals: int = 1, signed: bool = False) -> str:
    if v is None:
        return "—"
    sign = "+" if signed and v > 0 else ""
    return f"{sign}{v * 100:.{decimals}f}%"


def fmt_mult(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}x"


def fmt_money_bn(v: Optional[float], ccy: str) -> str:
    if v is None:
        return "—"
    return f"{ccy} {v / 1e9:,.1f}bn"


def fmt_slope_pp(v: Optional[float]) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v * 100:.2f}pp/yr"


def cell_class(cell: Optional[CompCell], higher_is_good: Optional[bool] = None) -> str:
    if cell is None or cell.value is None:
        return "muted"
    if higher_is_good is None:
        return ""
    if higher_is_good and cell.value > 0:
        return "pos"
    if higher_is_good and cell.value < 0:
        return "neg"
    if (not higher_is_good) and cell.value > 0:
        return "neg"
    if (not higher_is_good) and cell.value < 0:
        return "pos"
    return ""


def title_cell(cell: Optional[CompCell]) -> str:
    """Hover tooltip with provenance."""
    if cell is None:
        return ""
    bits: list[str] = []
    if cell.source:
        bits.append(f"source={cell.source}")
    if cell.retrieved_at:
        bits.append(f"retrieved={cell.retrieved_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if cell.note:
        bits.append(f"note={cell.note}")
    if cell.flag:
        bits.append(f"flag={cell.flag}")
    if cell.source_url:
        bits.append(f"url={cell.source_url}")
    return f' title="{html.escape(" | ".join(bits))}"' if bits else ""


def get(row: CompRow, key: str) -> Optional[CompCell]:
    return row.metrics.get(key)


# ---------------------------------------------------------------------------
# HTML render


_CSS = """
:root {
  --bg: #000000;
  --bg-alt: #0a0a0a;
  --bg-card: #0f0f0f;
  --gold: #FA8C00;
  --gold-soft: rgba(250, 140, 0, 0.25);
  --target: #1a3a5c;
  --target-soft: rgba(26, 58, 92, 0.4);
  --text: #e8e6e0;
  --text-muted: #6e6e6e;
  --pos: #00C853;
  --neg: #FF1744;
  --mono: 'JetBrains Mono', ui-monospace, Menlo, monospace;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.4;
}
header.bar {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid var(--gold-soft);
  gap: 24px;
}
header .title { color: var(--gold); font-weight: 600; letter-spacing: 0.06em; font-size: 13px; }
header .meta { font-size: 10px; color: var(--text-muted); text-align: right; }
header .draft {
  background: var(--gold); color: #1a1409;
  padding: 4px 10px; border-radius: 2px;
  font-size: 10px; letter-spacing: 0.18em;
  text-transform: uppercase; font-weight: 600;
}

.three-panel {
  display: grid;
  grid-template-columns: 1.05fr 1.15fr 0.95fr;
  gap: 1px;
  background: var(--gold-soft);
  min-height: calc(100vh - 50px);
}
.panel {
  background: var(--bg);
  padding: 16px 18px;
  overflow-x: auto;
}
.panel h2 {
  font-size: 11px; letter-spacing: 0.2em; text-transform: uppercase;
  color: var(--gold); margin: 0 0 6px;
  border-bottom: 1px solid var(--gold-soft);
  padding-bottom: 6px;
}
.panel .sub {
  font-size: 10px; color: var(--text-muted); margin-bottom: 14px;
}

table.comp {
  width: 100%; border-collapse: collapse;
  font-variant-numeric: tabular-nums;
}
table.comp th, table.comp td {
  padding: 4px 8px; vertical-align: top;
  border-bottom: 1px solid #1a1a1a;
}
table.comp th {
  font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--text-muted); font-weight: 500; text-align: right;
}
table.comp th:first-child, table.comp td:first-child {
  text-align: left; min-width: 130px;
}
table.comp td { text-align: right; font-size: 12px; }
table.comp tr.target { background: var(--target-soft); }
table.comp tr.target td { color: var(--text); font-weight: 500; }
table.comp tr.target_segment td {
  background: rgba(250, 140, 0, 0.04);
  font-style: italic;
  color: #c9c5b8;
}
table.comp tr.target_segment td:first-child::before {
  content: '↳ '; color: var(--gold-soft); font-style: normal;
}
.muted { color: var(--text-muted); }
.pos { color: var(--pos); }
.neg { color: var(--neg); }
.flag-data-pending { color: var(--gold); font-style: italic; }

.section { margin-top: 18px; }
.section h3 {
  font-size: 10px; letter-spacing: 0.2em; text-transform: uppercase;
  color: var(--gold); margin: 0 0 6px;
  font-weight: 500;
}

.cm-warning {
  border: 1px solid var(--gold-soft);
  background: rgba(250, 140, 0, 0.06);
  padding: 8px 10px; margin-bottom: 14px;
  font-size: 10px;
}
.cm-warning .lbl { color: var(--gold); text-transform: uppercase; letter-spacing: 0.18em; font-size: 9px; margin-bottom: 4px; }
.cm-warning ul { margin: 4px 0 0; padding-left: 16px; color: var(--text); }
.cm-warning li { margin-bottom: 2px; }

table.spread { width: 100%; border-collapse: collapse; font-size: 11px; }
table.spread th, table.spread td { padding: 5px 8px; border-bottom: 1px solid #1a1a1a; vertical-align: top; }
table.spread th { color: var(--text-muted); font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; text-align: right; font-weight: 500; }
table.spread th:first-child, table.spread td:first-child { text-align: left; }
table.spread td { text-align: right; font-variant-numeric: tabular-nums; }

.position-rich { color: var(--gold); font-weight: 500; }
.position-cheap { color: var(--pos); font-weight: 500; }
.position-neutral { color: var(--text-muted); }
.position-na { color: var(--text-muted); font-style: italic; }

.reset-bar { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center; margin-bottom: 6px; }
.reset-bar .label { font-size: 10px; color: var(--text); }
.reset-bar .value { font-size: 12px; font-weight: 500; }
.reset-bar .value.neg { color: var(--neg); }
.reset-bar .value.pos { color: var(--pos); }

.policy-table { font-size: 10px; }
.policy-table .score-pos { color: var(--pos); }
.policy-table .score-neg { color: var(--neg); }
.policy-table .score-neutral { color: var(--text-muted); }

details.commentary {
  border: 1px solid var(--gold-soft);
  padding: 10px 12px;
  margin-top: 8px;
  background: var(--bg-card);
  border-radius: 2px;
}
details.commentary summary {
  cursor: pointer;
  list-style: none;
  font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase;
  color: var(--gold);
}
details.commentary summary::-webkit-details-marker { display: none; }
details.commentary summary::before { content: '▸ '; }
details.commentary[open] summary::before { content: '▾ '; }
details.commentary .body { margin-top: 8px; font-size: 11px; line-height: 1.6; color: var(--text); }
.commentary-banner {
  background: var(--gold); color: #1a1409;
  padding: 6px 10px; font-size: 10px; letter-spacing: 0.15em;
  text-transform: uppercase; font-weight: 600; margin-bottom: 10px;
}

footer.bar {
  padding: 14px 20px;
  border-top: 1px solid var(--gold-soft);
  font-size: 10px;
  color: var(--text-muted);
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 24px;
  align-items: center;
}

@media (max-width: 1200px) {
  .three-panel { grid-template-columns: 1fr; }
}
"""


def render_pair_view(
    china_table: CompTable,
    global_table: CompTable,
    spread: SpreadAnalysis,
    commentary: Optional[Commentary],
    output_path: Path,
) -> Path:
    """Render the three-panel HTML and write to output_path. Returns the path."""

    title = (
        "SIEYUAN ELECTRIC (002028.SZ) | SIEMENS ENERGY (ENR.DE) — "
        "Pair-Trade Analysis"
    )
    generated = spread.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    is_draft = (commentary is None) or (not china_table.verified) or (not global_table.verified)
    draft_badge = '<span class="draft">DRAFT</span>' if is_draft else ""

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>UBS Pair Trade — Sieyuan vs Siemens Energy</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
<header class="bar">
  <div class="title">{html.escape(title)}</div>
  <div></div>
  <div class="meta">
    Generated {generated}<br>
    {draft_badge}
  </div>
</header>

<div class="three-panel">
  <section class="panel" id="panel-china">
    <h2>002028.SZ | Sieyuan Electric — China T&amp;D Basket</h2>
    <div class="sub">A-share basket · CAS reporting · 6 members</div>
    {_render_comp_table(china_table)}
  </section>

  <section class="panel" id="panel-global">
    <h2>ENR.DE | Siemens Energy — Global Power Basket</h2>
    <div class="sub">Multi-jurisdiction · IFRS / USGAAP / JGAAP · 6 members + segment row</div>
    {_render_comp_table(global_table)}
  </section>

  <section class="panel" id="panel-spread">
    <h2>Cross-Basket Spread</h2>
    <div class="sub">Pair-trade analytical artifact</div>

    {_render_cross_market_warning(spread)}

    <div class="section">
      <h3>Direct Pair · Sieyuan vs Siemens Energy</h3>
      {_render_direct_pair(spread.direct_pair)}
    </div>

    <div class="section">
      <h3>Basket-vs-Basket Median Spread</h3>
      {_render_basket_vs_basket(spread.basket_vs_basket)}
    </div>

    <div class="section">
      <h3>Relative Positioning · Sieyuan within China T&amp;D</h3>
      {_render_positioning(spread.relative_positioning_sieyuan)}
    </div>

    <div class="section">
      <h3>Relative Positioning · Siemens Energy within Global Power</h3>
      {_render_positioning(spread.relative_positioning_siemens)}
    </div>

    <div class="section">
      <h3>Valuation-Reset Sensitivity</h3>
      {_render_reset(spread.valuation_reset_sensitivity)}
    </div>

    <div class="section">
      <h3>Policy Exposure Overlay</h3>
      {_render_policy(spread.policy_exposure)}
    </div>

    <div class="section">
      <h3>Commentary</h3>
      {_render_commentary(commentary)}
    </div>
  </section>
</div>

<footer class="bar">
  <div>{_render_fx(china_table, global_table)}</div>
  <div>Lineage: outputs/lineage/run_{generated.replace(':', '').replace(' ', '_')}.json (placeholder)</div>
  <div>{draft_badge if is_draft else ''}</div>
</footer>

</body>
</html>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_doc, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Sub-renderers


_COMP_COLS = [
    ("price", "Price", lambda v: fmt_num(v, 2)),
    ("market_cap_usd", "Mcap (USD bn)", lambda v: fmt_num(v / 1e9, 1) if v else "—"),
    ("pct_1m", "1M %", lambda v: fmt_pct(v, 1, signed=True)),
    ("pct_ytd", "YTD %", lambda v: fmt_pct(v, 1, signed=True)),
    ("pe_fwd", "P/E (Fwd)", fmt_mult),
    ("pe_fwd_premium_to_median", "vs peer med", lambda v: fmt_pct(v, 0, signed=True)),
    ("ev_ebitda", "EV/EBITDA", fmt_mult),
    ("ev_ebitda_premium_to_median", "vs peer med", lambda v: fmt_pct(v, 0, signed=True)),
    ("ebit_margin", "EBIT margin", fmt_pct),
    ("ebit_margin_trajectory_slope", "Margin slope", fmt_slope_pp),
    ("rev_yoy", "Rev YoY", lambda v: fmt_pct(v, 1, signed=True)),
    ("rev_cagr_3y", "3Y CAGR", lambda v: fmt_pct(v, 1, signed=True)),
    ("roe", "ROE", fmt_pct),
]


def _render_comp_table(table: CompTable) -> str:
    headers = "".join(f'<th>{html.escape(h)}</th>' for _, h, _ in _COMP_COLS)
    rows_html = []
    for r in table.rows:
        tr_class = r.role
        first = (
            f'<td><strong>{html.escape(r.ticker)}</strong>'
            f' &nbsp; <span class="muted">{html.escape(r.name)}</span></td>'
        )
        cells_html = []
        for key, _, fmt in _COMP_COLS:
            cell = get(r, key)
            if cell is None or cell.value is None:
                if cell and cell.flag == "DATA_PENDING":
                    cells_html.append(
                        f'<td class="muted flag-data-pending"{title_cell(cell)}>data pending</td>'
                    )
                else:
                    note = (cell.note if cell else None) or "—"
                    cells_html.append(
                        f'<td class="muted"{title_cell(cell)}>{html.escape(note) if cell and cell.note else "—"}</td>'
                    )
                continue
            try:
                rendered = fmt(cell.value)
            except Exception:  # noqa: BLE001
                rendered = "—"
            cls = ""
            # Color premium-to-median in pair-trade-relevant direction
            if "_premium_to_median" in key:
                cls = "neg" if cell.value > 0 else "pos" if cell.value < 0 else "muted"
            elif key == "ebit_margin_trajectory_slope":
                cls = "pos" if cell.value > 0 else "neg" if cell.value < 0 else "muted"
            cells_html.append(f'<td class="{cls}"{title_cell(cell)}>{rendered}</td>')
        rows_html.append(f'<tr class="{tr_class}">{first}{"".join(cells_html)}</tr>')

    return (
        '<table class="comp"><thead><tr>'
        '<th></th>' + headers + '</tr></thead><tbody>'
        + "\n".join(rows_html)
        + '</tbody></table>'
    )


def _render_cross_market_warning(spread: SpreadAnalysis) -> str:
    asyms = (spread.cross_market_caveats or {}).get("key_asymmetries", [])
    if not asyms:
        return ""
    items = "".join(f"<li>{html.escape(a)}</li>" for a in asyms)
    return (
        '<div class="cm-warning">'
        '<div class="lbl">⚠ Cross-Market Asymmetry</div>'
        f'<ul>{items}</ul>'
        '</div>'
    )


_METRIC_LABELS = {
    "ev_sales": "EV/Sales",
    "ev_ebitda": "EV/EBITDA",
    "pe_ttm": "P/E (TTM)",
    "pe_fwd": "P/E (Fwd)",
    "fcf_yield": "FCF Yield",
    "ebit_margin": "EBIT margin",
    "ebitda_margin": "EBITDA margin",
    "rev_yoy": "Rev YoY",
    "rev_cagr_3y": "3Y CAGR",
    "roe": "ROE",
    "net_debt_ebitda": "Net Debt / EBITDA",
    "pb": "P/B",
}


def _fmt_metric_value(metric: str, v: Optional[float]) -> str:
    if v is None:
        return "—"
    if metric in ("ebit_margin", "ebitda_margin", "rev_yoy", "rev_cagr_3y", "roe", "fcf_yield"):
        return fmt_pct(v, 1)
    return fmt_mult(v)


def _render_direct_pair(lines: list[DirectPairLine]) -> str:
    rows = []
    for ln in lines:
        rows.append(
            "<tr>"
            f"<td>{_METRIC_LABELS.get(ln.metric, ln.metric)}</td>"
            f"<td>{_fmt_metric_value(ln.metric, ln.sieyuan_value)}</td>"
            f"<td>{_fmt_metric_value(ln.metric, ln.enr_consolidated_value)}</td>"
            f"<td>{_fmt_metric_value(ln.metric, ln.enr_grid_segment_value)}</td>"
            "</tr>"
        )
    return (
        '<table class="spread"><thead><tr>'
        '<th></th><th>Sieyuan</th><th>ENR cons.</th><th>ENR Grid</th>'
        '</tr></thead><tbody>'
        + "\n".join(rows) + '</tbody></table>'
    )


def _render_basket_vs_basket(lines: list[DirectPairLine]) -> str:
    rows = []
    for ln in lines:
        rows.append(
            "<tr>"
            f"<td>{_METRIC_LABELS.get(ln.metric, ln.metric)}</td>"
            f"<td>{_fmt_metric_value(ln.metric, ln.sieyuan_value)}</td>"  # china median
            f"<td>{_fmt_metric_value(ln.metric, ln.enr_consolidated_value)}</td>"  # global median
            f"<td>{_fmt_metric_value(ln.metric, ln.raw_spread_consolidated)}</td>"
            "</tr>"
        )
    return (
        '<table class="spread"><thead><tr>'
        '<th></th><th>China median</th><th>Global median</th><th>Spread</th>'
        '</tr></thead><tbody>'
        + "\n".join(rows) + '</tbody></table>'
    )


def _render_positioning(items: list[RelativePositioning]) -> str:
    rows = []
    for it in items:
        pos_class = {
            "RICH": "position-rich",
            "CHEAP": "position-cheap",
            "NEUTRAL": "position-neutral",
            "n/a": "position-na",
        }[it.target_position]
        pct = ""
        if it.percentile_within_basket is not None:
            pct = f" ({it.percentile_within_basket * 100:.0f}th pct)"
        rows.append(
            "<tr>"
            f"<td>{_METRIC_LABELS.get(it.metric, it.metric)}</td>"
            f"<td>{_fmt_metric_value(it.metric, it.target_value)}</td>"
            f"<td>{_fmt_metric_value(it.metric, it.basket_median_excl_target)}</td>"
            f'<td class="{pos_class}">{it.target_position}{pct}</td>'
            "</tr>"
        )
    return (
        '<table class="spread"><thead><tr>'
        '<th></th><th>Target</th><th>Peer median</th><th>Position</th>'
        '</tr></thead><tbody>'
        + "\n".join(rows) + '</tbody></table>'
    )


def _render_reset(items: list[ResetSensitivity]) -> str:
    out = []
    for it in items:
        v = it.implied_pct_move_to_median
        cls = "neg" if (v is not None and v < 0) else "pos" if v else "muted"
        if v is None:
            disp = "—"
        elif "absolute pp" in (it.note or ""):
            disp = f"{v * 100:+.1f}pp"
        else:
            disp = f"{v * 100:+.1f}%"
        out.append(
            '<div class="reset-bar">'
            f'<div class="label">{html.escape(it.target_label)}</div>'
            f'<div class="value {cls}">{disp}</div>'
            '</div>'
        )
        if it.note:
            out.append(f'<div class="muted" style="font-size:9px;margin:-3px 0 8px;">{html.escape(it.note)}</div>')
    return "".join(out) or '<div class="muted">no reset sensitivity computed</div>'


def _render_policy(entries: list[PolicyEntry]) -> str:
    if not entries:
        return (
            '<div class="muted" style="font-size:10px;">'
            'Policy exposure YAML not yet populated. '
            'Fill <code>data/policy/exposure_2026.yaml</code> with citations.'
            '</div>'
        )
    rows = []
    for e in entries:
        sc = e.exposure_score
        cls = "score-pos" if (sc is not None and sc > 0.1) else "score-neg" if (sc is not None and sc < -0.1) else "score-neutral"
        sc_str = f"{sc:+.2f}" if sc is not None else "—"
        rows.append(
            "<tr>"
            f"<td>{html.escape(e.policy)}</td>"
            f"<td>{html.escape(e.target_ticker)}</td>"
            f'<td class="{cls}">{sc_str}</td>'
            f"<td>{html.escape((e.rationale or '')[:80])}</td>"
            "</tr>"
        )
    return (
        '<table class="spread policy-table"><thead><tr>'
        '<th>Policy</th><th>Target</th><th>Score</th><th>Rationale</th>'
        '</tr></thead><tbody>'
        + "\n".join(rows) + '</tbody></table>'
    )


def _render_commentary(commentary: Optional[Commentary]) -> str:
    if commentary is None:
        return (
            '<div class="muted">Commentary not yet drafted. Run pipeline '
            'with --with-commentary or fill the YAMLs first.</div>'
        )
    parts = [
        f'<div class="commentary-banner">{html.escape(commentary.draft_banner)}</div>',
        f'<div style="margin-bottom:10px;font-style:italic;">{html.escape(commentary.one_line_summary)}</div>',
    ]
    sections = [
        ("Direct Pair Observations", commentary.direct_pair_observations),
        ("Basket-Relative Positioning", commentary.basket_relative_positioning),
        ("Trajectory Asymmetry", commentary.trajectory_asymmetry_observations),
        ("Valuation-Reset Observations", commentary.valuation_reset_observations),
        ("Data Flags", commentary.data_flags),
        ("Limitations", commentary.limitations),
    ]
    for title, text in sections:
        if not text:
            continue
        parts.append(
            '<details class="commentary">'
            f'<summary>{html.escape(title)}</summary>'
            f'<div class="body">{html.escape(text).replace(chr(10), "<br>")}</div>'
            '</details>'
        )
    return "".join(parts)


def _render_fx(china_table: CompTable, global_table: CompTable) -> str:
    rates: dict[str, float] = {}
    rates.update(china_table.fx_rates_used)
    rates.update(global_table.fx_rates_used)
    parts = [f"FX (USD per unit): " + " · ".join(f"{ccy}={rate:.4f}" for ccy, rate in sorted(rates.items()))]
    return " | ".join(parts)

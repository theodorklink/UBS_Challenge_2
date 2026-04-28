# UBS Valuation Tool

> Bloomberg-style pair-trade comparison tool for the **2026 UBS Finance Challenge**.
> Built as a quantitative complement to a separate AI research module — the two tools are intentionally decoupled.

**Pair Trade**

- **LONG** Siemens Energy (`ENR.DE`) — focus on the **Grid Technologies** segment
- **SHORT** Sieyuan Electric (`002028.SZ`) — Chinese private-sector grid equipment maker

Direction is committed; the tool itself is **data-only**. The narrative emerges from how spreads are framed (premium / discount to peer median, trajectory slopes, valuation-reset sensitivity), never from tool-generated opinions.

---

## What it produces

A single self-contained HTML file with **three Bloomberg-style panels**:

| Panel | Content |
|---|---|
| **Left** | Sieyuan + China T&D peer basket (NARI, XJ, Pinggao, TBEA, China XD) |
| **Middle** | Siemens Energy + Global Power peer basket (GE Vernova, Schneider, Hitachi, Eaton, MHI) — with a separate Grid Technologies segment row populated from manually-transcribed IR data |
| **Right** | Cross-basket spread analysis: direct pair, basket-vs-basket median spread, relative positioning (RICH / NEUTRAL / CHEAP), valuation-reset sensitivity, policy exposure overlay, AI-drafted commentary |

Plus a structured `cross_market_caveats` block surfacing A-share vs European reporting / accounting / fiscal-year-end / liquidity asymmetries — never papered over.

---

## Sample output (from a recent run)

**Live data, fetched 2026-04-28:**

| Metric | Sieyuan (002028.SZ) | Siemens Energy (ENR.DE) |
|---|---:|---:|
| Price | CNY 195.25 | EUR 172.98 |
| Mcap (USD bn) | 22.3 | 173.4 |
| YTD % | +29.2% | +40.9% |
| Forward P/E | 30.3x | 30.3x |
| **vs peer median** | **+59%** (RICH) | **−17%** (NEUTRAL) |
| EBIT margin | 18.0% | 4.0% |
| EBIT margin slope (3Y) | +1.3 pp/yr | **+2.95 pp/yr** |

**Cross-basket spread highlights:**

- Sieyuan sits at the **100th percentile** of its China T&D basket on EV/Sales, EV/EBITDA, revenue growth, EBIT margin, and ROE — labelled `RICH` on every metric.
- Siemens Energy sits **broadly NEUTRAL** within Global Power despite a materially lower margin base; consolidated forward P/E is below basket median (Global Power median is being pulled up by GE Vernova post-spinoff multiples).
- **Valuation-reset sensitivity:** if Sieyuan's forward P/E reverts to its China T&D basket median, implied move is **−37%**. If Siemens Energy's reverts to its Global Power median, implied move is **+20%**. *The spread is the trade.*
- **Trajectory asymmetry:** Siemens Energy's EBIT margin is expanding at +2.95pp/yr — roughly **2.2× the Global Power basket median slope**. Sieyuan is at +1.3pp/yr against a flatter China T&D basket.

These observations are produced by the tool from yfinance + manually curated segment data — no qualitative judgement is applied. The asymmetry is structural in the data.

---

## Architecture

```
ubs-valuation-tool/
├── src/
│   ├── data_sources/
│   │   ├── yfinance_adapter.py        # Stage 1a: live fundamentals
│   │   ├── siemens_energy_segments.py # Stage 1c: segment YAML loader
│   │   └── models.py                  # Provenance Pydantic models
│   ├── comparables/
│   │   └── comp_builder.py            # Stage 3: dual-basket comp tables
│   ├── spread/
│   │   └── cross_basket.py            # Stage 4: spread + reset sensitivity
│   ├── summary/
│   │   └── commentary.py              # Stage 5: Anthropic-drafted commentary
│   ├── render/
│   │   └── bloomberg_view.py          # Stage 6: 3-panel HTML
│   └── cli.py                         # Orchestrator
├── data/
│   ├── universes/
│   │   ├── china_td.yaml              # Sieyuan + 5 peer basket
│   │   └── global_power.yaml          # Siemens Energy + 5 peer basket
│   ├── raw/siemens_energy/            # Manual segment YAMLs (FY2024, FY2025-Q1/Q2)
│   └── policy/exposure_2026.yaml      # CBAM / FSR / Section 232 / IRA / Stock Connect
├── outputs/
│   ├── comp_tables/                   # Per-basket JSON (gitignored)
│   ├── spread_analysis/               # Cross-basket JSON
│   ├── valuation_drafts/              # Commentary drafts
│   ├── views/                         # Rendered HTML
│   └── lineage/                       # Audit-trail JSONs (planned)
├── scripts/
│   ├── smoke_test.py                  # Verify data sources
│   └── rerender.py                    # Re-render HTML from existing JSONs
├── tests/
└── CLAUDE.md                          # Project context + hard rules
```

---

## Setup

```bash
git clone https://github.com/theodorklink/UBS_Challenge_2.git
cd UBS_Challenge_2

python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: paste ANTHROPIC_API_KEY (for the Commentary drafter, Stage 5)
#            FMP_API_KEY is optional (cross-validation, free tier)
```

---

## Usage

### Run the full pipeline

```bash
python -m src.cli run
```

Pipeline (5 stages, ~90s end-to-end):

1. **Build China T&D comp table** — Sieyuan + 5 peers via yfinance
2. **Build Global Power comp table** — Siemens Energy + 5 peers + Grid Tech segment row
3. **Cross-basket spread analysis** — direct pair, basket-vs-basket, relative positioning, reset sensitivity, policy overlay
4. **Commentary draft** — Anthropic Claude Sonnet 4.6, with strict safety perimeter
5. **Render Bloomberg HTML** — single self-contained file

Output: `outputs/views/sieyuan_vs_siemens_<timestamp>.html` (open in any browser).

### Skip the LLM step

```bash
python -m src.cli run --no-commentary
```

### Re-render only (after fixing render bugs without re-fetching data)

```bash
python scripts/rerender.py
```

### Preview server (local viewing)

```bash
cd outputs/views && python -m http.server 8001
# Open http://localhost:8001/
```

---

## Hard rules (encoded in code)

These are non-negotiable safety constraints, defined in [`CLAUDE.md`](CLAUDE.md):

| Rule | Where enforced |
|---|---|
| No DCF generated by the LLM | System prompt + postcheck |
| No fabricated tickers | yfinance `verify_ticker()` |
| Every numeric output carries `source` + `retrieved_at` + `source_url` | Pydantic `Provenance` mixin |
| Commentary drafter cannot output `long`, `short`, `buy`, `sell`, `overweight`, `underweight`, `outperform`, `underperform`, `target price`, `fair value`, `we recommend`, `trade idea` — case-insensitive postcheck blocks save | `src/summary/commentary.py:_FORBIDDEN_RE` |
| Cross-market comparisons display the structured `cross_market_caveats` object | `bloomberg_view._render_cross_market_warning()` |
| Siemens Energy comparisons to Sieyuan default to **Grid Technologies segment** | `comp_builder._build_grid_tech_segment_row()` |
| Missing data fails loudly — never imputed | renderer shows "—" / "data pending" |

---

## Narrative-supporting framing

The tool surfaces these as standard pair-trade analytics — never as opinions:

- **Premium / discount to peer median** as the pair-trade-relevant valuation view
- **3Y trajectory slope** on margins (linear regression, annual obs; semi-annual for Sieyuan)
- **Valuation-reset sensitivity** (revert each target to its basket median forward P/E)
- **Order quality composite** (book-to-bill × backlog/revenue, where disclosed)
- **Policy exposure overlay** with citations (CBAM, FSR, Section 232, IRA, Stock Connect)

If the data points the other way on any single metric, the tool shows it. The output is honest.

---

## Data sources

| Source | Used for | Cost |
|---|---|---|
| **yfinance** | All 12 basket members (incl. A-shares via `.SS` / `.SZ`) | Free |
| **akshare** | Optional A-share fallback (planned) | Free |
| **Anthropic Claude Sonnet 4.6** | Commentary drafter | ~$0.20-0.30 per run |
| **Manual YAML** | Siemens Energy Grid Tech segment data with page citations from IR PDFs | Manual entry |
| **Financial Modeling Prep** (optional) | Cross-validation of yfinance values | Free tier 250/day |

---

## Known limitations

- **Sieyuan reports semi-annually** (CSRC permits) — trajectory slopes computed on 6 semi-annual observations rather than 12 quarterly. Auto-flagged in `CompanyInfo.reporting_frequency`.
- **A-share forward estimates are sparse on yfinance** — Western analyst coverage is limited. Forward P/E shows "n/a — limited Western analyst coverage" for affected peers.
- **EV multiples are not segment-allocable** — the Grid Tech segment row shows operating metrics (margin, growth, book-to-bill) only; EV-based multiples are blank with the explicit caveat.
- **Mitsubishi Heavy is a stretch peer** — power business is one of three segments; flagged in basket validator output.

---

## License

MIT — see LICENSE file (or use freely for academic / competition purposes).

---

## Acknowledgements

Built for the 2026 UBS Finance Challenge submission. Sister project: [`ubs-ai-module`](https://github.com/theodorklink/ubs-ai-module) (narrative research tool, Tavily + Claude, Vercel-deployed).

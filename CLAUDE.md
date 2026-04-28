# UBS Valuation Tool — Project Context for Claude Code

## Mission

Quantitative pair-trade comparison tool for the 2026 UBS Finance Challenge.
Produces a Bloomberg-style three-panel HTML view comparing two targets
across two regional peer baskets, with rigorous source attribution.

This is a **second tool**, separate from the existing `ubs-ai-module/`
project (which is a narrative/research tool). They are not merged.

## The Pair Trade

- **LONG**: Siemens Energy (`ENR.DE`), with explicit focus on the
  **Grid Technologies** segment.
- **SHORT**: Sieyuan Electric (`002028.SZ`), Chinese private-sector grid
  equipment maker.

**Direction is committed; this is the analytical lens.**

The tool itself must remain **data-only and methodologically defensible**.
Judges will ask "where does this number come from"; the answer must always
be a verifiable source. The tool **must never state long/short
recommendations directly**. The narrative comes from how we frame and
visualize spreads (premium/discount to peer median, margin trajectory
differentials, valuation-reset sensitivity), not from the tool generating
opinions.

## Three Analytical Constraints (drive every design decision)

1. **Sieyuan and Siemens Energy are not direct peers.** Sieyuan is a focused
   Chinese T&D pure-play (~RMB 18bn revenue). Siemens Energy is a €34bn
   diversified conglomerate with four segments. The relevant cross-comparison
   is **Sieyuan vs Siemens Energy Grid Technologies segment**. The tool
   supports segment-level financials.

2. **A-share vs European multiples differ structurally** (retail flow,
   capital controls, Stock Connect, domestic liquidity premium, accounting
   CAS vs IFRS, fiscal year end Dec vs Sept, semi-annual vs quarterly
   reporting). Every cross-market comparison surfaces a **structured
   `cross_market_caveats` object**, never a vague text paragraph.

3. **Peer benchmarking is dual-basket, not single.** The pair-trade
   artifact lives in the **cross-basket spread**, not in the direct
   two-stock comparison.

## Architecture (5 modules + verify + render)

```
Module 1  Basket Validator        (src/screener/)
Module 2  Comp Table Builder      (src/comparables/)
Module 3  Cross-Basket Spread     (src/spread/)
Module 4  Commentary Drafter      (src/summary/)
Module 5  Bloomberg View          (src/render/)
Tool      Spot-Check Verify       (src/verify.py)
Tool      Slide Export            (src/render/slide_export.py)
CLI       Orchestrator            (src/cli.py)
```

Each module has a single responsibility and consumes the prior module's
**verified** output. Verification is the gate; downstream refuses
unverified upstream input (override flag for dev only).

## Hard Rules (non-negotiable, enforced in code)

- **No DCF generation by the LLM. Ever.**
- **No fabricated tickers.** Every ticker is verified before use.
- **Every numeric output carries `source`, `retrieved_at`, `source_url`/
  `source_page`.**
- **Missing data fails loudly.** Never impute, never silently drop.
  Display "n/a — limited coverage" in human output, leave field `null`
  in JSON.
- **Module 4 (Commentary) outputs DRAFTS for human edit, never final
  language.** Wrapped with `*** DRAFT — REQUIRES HUMAN REVIEW — NOT A
  TRADE RECOMMENDATION ***` banner. **Forbidden tokens** in commentary
  output (case-insensitive postcheck refuses save if found):
  `long`, `short`, `buy`, `sell`, `overweight`, `underweight`,
  `outperform`, `underperform`, `target price`, `fair value`,
  `we recommend`, `trade idea`.
- **Cross-market comparisons** (A-share vs European) ALWAYS display
  the structured `cross_market_caveats` object visually.
- **Siemens Energy comparisons to Sieyuan default to Grid Technologies
  segment level.** Consolidated values shown alongside but never as the
  primary peer comparison.

## Data Source Routing

| Suffix / Context        | Primary           | Cross-Validation |
|-------------------------|-------------------|------------------|
| `.DE`, `.PA`, `.T`, `.L`, US (no suffix) | yfinance | FMP free tier |
| `.SS`, `.SZ` (A-shares) | yfinance          | akshare fallback |
| Siemens Energy segments | Manual YAML       | pdfplumber automated extraction |

Note: yfinance uses `.SS` (not `.SH`) for Shanghai. The unified router
normalizes between human-form `600406.SH` and yfinance-form `600406.SS`.

Every adapter call is logged at INFO level with `served_by: <source>`.

## Lineage / Audit Trail

Every full pipeline run produces `outputs/lineage/run_{timestamp}.json`
mapping each numeric value in the final HTML back to:
`(adapter, cache_file, raw_source_url, raw_source_page_or_field,
retrieved_at)`.

This is the audit trail for live judge Q&A: "where does this number come
from?" → answer in <5 seconds from the lineage file.

## Spot-Check Protocol (out of orchestrator)

`src/verify.py` is a standalone tool. Workflow:

```
python -m src.verify outputs/comp_tables/china_td_002028.SZ_2026-04-28.json
```

Reads the JSON, prints `(ticker, metric, value, source URL, page)` for
≥2 cells per peer, awaits human input `verified` / `rejected: <reason>` /
`skip`, saves verification log alongside comp table, marks
`verified: true` only when all loops pass.

The orchestrator refuses downstream modules if any input is unverified.
Dev-mode override: `--allow-unverified` (watermarks all output as
"DEV — UNVERIFIED").

## Visual Style (Bloomberg Homage)

- Background: true black `#000000`
- Mono font: JetBrains Mono via Google Fonts CDN
- Amber accent: `#FA8C00`
- Target row highlight: deep blue `#1a3a5c`
- Positive: green `#00C853`
- Negative: red `#FF1744`
- Excluded / n/a values: muted grey `#6E6E6E`
- Three-panel layout: Sieyuan + China basket | Siemens Energy + Global
  basket | Cross-basket spread
- Cross-market warning: small amber-bordered box, never a full-width
  banner
- Tight row height (~22px), right-aligned numerics, hover tooltips on
  every cell showing full provenance

## Narrative-Aware Metric Ordering

The metrics within comp tables and spread output are ordered to surface
the pair's analytical asymmetries naturally. The tool **does not say**
"this favors LONG Siemens" — it shows the data with framings that let
the human draw the conclusion.

1. **Valuation metrics** show absolute values AND
   `premium_discount_to_peer_median` — the latter is the pair-trade
   relevant view. Sieyuan's premium-to-China-T&D-median vs Siemens
   Energy's premium-to-Global-Power-median is the core spread.

2. **Operating metrics** show level AND **3Y trajectory slope** (linear
   regression through the last 12 quarterly observations; or 6 semi-
   annual for Sieyuan). Trajectory asymmetry is what differentiates
   "improving incumbent" from "decelerating challenger."

3. **Order-book quality composite** (book-to-bill × backlog/revenue ×
   backlog growth). For Sieyuan partial (semi-annual disclosure); for
   Siemens fully observable from quarterly disclosure.

4. **Valuation-reset sensitivity**: implied price impact if multiple
   reverts to peer basket median. The asymmetry between Sieyuan's number
   and Siemens Energy's number is the pair-trade core spread.

5. **Policy exposure overlay**: hand-filled YAML (`data/policy/
   exposure_2026.yaml`) with per-company exposure scores for CBAM, FSR,
   Section 232, IRA, Stock Connect outflow risk. Each entry has a
   citation. Tool surfaces these alongside, does not score or sum them.

## Output Schema Conventions

All modules emit Pydantic models. Each model carries:

```python
source: str              # adapter name e.g. "yfinance", "akshare", "fmp"
retrieved_at: datetime   # ISO 8601 timestamp
source_url: HttpUrl | None       # web URL where applicable
source_page: int | None          # page number for PDF sources
cross_validation_status: Literal["match", "mismatch", "unavailable"]
```

Currency: each model carries native currency. Conversion happens in the
comp table layer with the FX rate stored explicitly.

## Models / LLM Provider

The Commentary drafter (Stage 5) is the only LLM-touched module.

- Default: Anthropic Claude Sonnet 4.6 (`claude-sonnet-4-6`).
- Configured via `LLM_MODEL` env var.
- The system prompt is the safety perimeter. Reviewed line-by-line
  before wiring.
- Forbidden-token postcheck blocks save if it finds any of the banned
  tokens.

## Caching

`data/raw/{adapter}/{ticker}_{method}_{date}.json` with TTLs:

- Quotes: 24h
- Financials: 7d
- Estimates: 1d
- FMP cross-checks: 7d (cheaper to keep)

Caches are gitignored. Every cache hit is logged.

## Reporting Frequency Asymmetry

- **Sieyuan** reports semi-annually (CSRC permits). Latest quarterly
  data sparse. Trajectory slopes computed on 6 semi-annual observations
  rather than 12 quarterly. Adapter sets
  `reporting_frequency: 'semi_annual'` on Sieyuan's CompanyInfo.
- **Siemens Energy** reports quarterly with full segment disclosure.
  Trajectory slopes computed on 12 quarterly observations.

This asymmetry is itself an analytical observation worth surfacing in
the deck.

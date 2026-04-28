"""
Comp table builder — Stage 3.

Pulls data via yfinance for every basket member, computes valuation /
operating multiples, plus narrative-relevant framings:
  - premium / discount to peer median (excluding target) for selected
    multiples — the pair-trade-relevant view
  - 3-year EBIT-margin trajectory slope (linear regression on annual
    observations) — improvement vs plateau asymmetry
  - Order-quality composite where book-to-bill is disclosed (else None)

For the global_power basket with target ENR.DE: an extra row is emitted
for the Siemens Energy Grid Technologies SEGMENT, populated from
manual YAML (data/raw/siemens_energy/segments_*.yaml). If the YAMLs
are not yet filled, the segment row is emitted with DATA_PENDING flags
and downstream rendering shows "data pending — fill segment YAML".
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from ..data_sources.models import CompanyInfo, FinancialPeriod, Financials
from ..data_sources.yfinance_adapter import DataSourceError, YFinanceAdapter
from ..data_sources.siemens_energy_segments import (
    SegmentFinancials,
    list_available_periods,
    load_segment_data_manual,
)
from .models import CompCell, CompRow, CompTable

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_UNIVERSES_DIR = _REPO_ROOT / "data" / "universes"


# Approximate FX rates (USD per unit of native currency).
# Used as fallback if yfinance FX fetch fails.
_FX_FALLBACK = {
    "USD": 1.00,
    "EUR": 1.07,
    "CNY": 0.137,
    "JPY": 0.0067,
    "GBP": 1.27,
    "HKD": 0.128,
}


# ---------------------------------------------------------------------------
# Public API


class CompTableBuilder:
    """Builds a CompTable for one basket + target."""

    def __init__(self, adapter: Optional[YFinanceAdapter] = None):
        self.yf = adapter or YFinanceAdapter()
        self.fx_rates_usd: dict[str, float] = {}

    def build(self, basket_name: str, target_ticker: str) -> CompTable:
        """Build a comp table for the given basket and highlight the target."""
        basket = _load_basket_yaml(basket_name)
        target_in_basket = any(m["ticker"] == target_ticker for m in basket["members"])
        if not target_in_basket:
            raise ValueError(f"target {target_ticker} is not in basket {basket_name}")

        rows: list[CompRow] = []
        log.info("Building comp table: basket=%s target=%s (%d members)",
                 basket_name, target_ticker, len(basket["members"]))

        # ---- Pull raw data per member ------------------------------------
        member_data: list[tuple[dict, CompanyInfo, dict, Financials]] = []
        for m in basket["members"]:
            yf_ticker = m["yfinance_ticker"]
            try:
                ci = self.yf.get_company_info(yf_ticker)
                quote = self.yf.get_quote(yf_ticker)
                fin = self.yf.get_financials(yf_ticker)
                est = self.yf.get_estimates(yf_ticker)
            except DataSourceError as e:
                log.warning("Skipping %s — data source error: %s", yf_ticker, e)
                continue
            self._record_fx(ci.currency)
            member_data.append((m, ci, {"quote": quote, "estimates": est}, fin))

        # ---- Compute per-row metrics ------------------------------------
        for m, ci, extras, fin in member_data:
            role = "target" if m["ticker"] == target_ticker else "peer"
            row = self._build_row(m, ci, extras, fin, role)
            rows.append(row)

        # ---- Add Siemens Energy Grid Technologies segment row ----------
        # (only for the global_power basket with target ENR.DE)
        if basket_name == "global_power" and target_ticker == "ENR.DE":
            segment_row = self._build_grid_tech_segment_row(rows)
            # Insert directly after the consolidated ENR.DE row
            for i, r in enumerate(rows):
                if r.ticker == "ENR.DE" and r.role == "target":
                    rows.insert(i + 1, segment_row)
                    break

        # ---- Compute peer-median-relative metrics ----------------------
        self._populate_premium_to_median(rows, target_ticker)

        # ---- Cross-market caveats (only meaningful when comp tables are
        #      consumed jointly, but stored on the table for downstream)  -
        cross_caveats = _cross_market_caveats(basket)

        return CompTable(
            basket=basket_name,
            target_ticker=target_ticker,
            rows=rows,
            fx_rates_used=self.fx_rates_usd.copy(),
            cross_market_caveats=cross_caveats,
            generated_at=datetime.now(timezone.utc),
            verified=False,
        )

    # ---- Per-row helpers ---------------------------------------------

    def _build_row(
        self, m: dict, ci: CompanyInfo, extras: dict, fin: Financials, role: str
    ) -> CompRow:
        quote = extras["quote"]
        est = extras["estimates"]

        annual = fin.annual or []
        latest = annual[0] if annual else None
        prior = annual[1] if len(annual) > 1 else None

        # Native-currency values
        market_cap = ci.market_cap
        revenue = latest.revenue if latest else None
        ebitda = latest.ebitda if latest else None
        ebit = latest.operating_income if latest else None
        net_income = latest.net_income if latest else None
        equity = latest.total_equity if latest else None
        total_debt = latest.total_debt if latest else None
        cash = latest.cash_and_equivalents if latest else None
        minority = latest.minority_interest if latest else 0.0
        preferred = latest.preferred_equity if latest else 0.0
        fcf = latest.free_cash_flow if latest else None
        capex = latest.capex if latest else None
        gross = latest.gross_profit if latest else None

        # EV bridge (native currency)
        ev = None
        if market_cap is not None and total_debt is not None and cash is not None:
            ev = market_cap + total_debt - cash + (minority or 0.0) + (preferred or 0.0)

        # Multiples
        ev_sales = (ev / revenue) if (ev and revenue) else None
        ev_ebitda = (ev / ebitda) if (ev and ebitda and ebitda > 0) else None
        pe_ttm = (market_cap / net_income) if (market_cap and net_income and net_income > 0) else None
        pe_fwd = None
        if quote.price and est.fy1_eps_consensus:
            pe_fwd = quote.price / est.fy1_eps_consensus
        pb = (market_cap / equity) if (market_cap and equity and equity > 0) else None
        fcf_yield = (fcf / market_cap) if (fcf is not None and market_cap) else None
        # yfinance does not expose dividend yield reliably here; skip.

        # Operating
        gross_margin = (gross / revenue) if (gross is not None and revenue) else None
        ebitda_margin = (ebitda / revenue) if (ebitda is not None and revenue) else None
        ebit_margin = (ebit / revenue) if (ebit is not None and revenue) else None
        net_margin = (net_income / revenue) if (net_income is not None and revenue) else None
        roe = (net_income / equity) if (net_income is not None and equity and equity > 0) else None
        net_debt = ((total_debt or 0.0) - (cash or 0.0))
        net_debt_ebitda = (net_debt / ebitda) if (ebitda and ebitda > 0) else None

        # Growth
        rev_yoy = None
        if revenue and prior and prior.revenue and prior.revenue > 0:
            rev_yoy = revenue / prior.revenue - 1
        rev_cagr_3y = None
        if len(annual) >= 4 and annual[3].revenue and annual[3].revenue > 0 and revenue:
            rev_cagr_3y = (revenue / annual[3].revenue) ** (1 / 3) - 1

        # Trajectory slope (EBIT margin, last 4 annual periods,
        # percentage points per year — positive = improving)
        slope_ebit = _annual_margin_slope(annual, "operating_income")
        slope_ebitda = _annual_margin_slope(annual, "ebitda")

        # USD market cap
        market_cap_usd = None
        if market_cap is not None:
            fx = self.fx_rates_usd.get(ci.currency, _FX_FALLBACK.get(ci.currency))
            if fx is not None:
                market_cap_usd = market_cap * fx

        retrieved_at = datetime.now(timezone.utc)

        def cell(val, note: Optional[str] = None) -> CompCell:
            return CompCell(
                value=val, source="yfinance", retrieved_at=retrieved_at,
                note=note,
            )

        metrics: dict[str, CompCell] = {
            # Identification
            "market_cap_native": cell(market_cap),
            "market_cap_usd": cell(market_cap_usd),
            # Trading
            "price": cell(quote.price),
            "pct_1m": cell(quote.pct_1m),
            "pct_3m": cell(quote.pct_3m),
            "pct_1y": cell(quote.pct_1y),
            "pct_ytd": cell(quote.pct_ytd),
            # Valuation
            "ev_sales": cell(ev_sales),
            "ev_ebitda": cell(ev_ebitda),
            "pe_ttm": cell(pe_ttm),
            "pe_fwd": cell(
                pe_fwd,
                note="n/a — limited Western analyst coverage" if pe_fwd is None and ci.currency == "CNY" else None,
            ),
            "pb": cell(pb),
            "fcf_yield": cell(fcf_yield),
            # Operating
            "rev_yoy": cell(rev_yoy),
            "rev_cagr_3y": cell(rev_cagr_3y),
            "gross_margin": cell(gross_margin),
            "ebitda_margin": cell(ebitda_margin),
            "ebit_margin": cell(ebit_margin),
            "net_margin": cell(net_margin),
            "roe": cell(roe),
            "net_debt_ebitda": cell(net_debt_ebitda),
            # Trajectory
            "ebit_margin_trajectory_slope": cell(
                slope_ebit, note="annual obs; positive = improving",
            ),
            "ebitda_margin_trajectory_slope": cell(slope_ebitda),
        }

        notes: list[str] = []
        if ci.reporting_frequency == "semi_annual":
            notes.append(
                "semi-annual reporting (CSRC); trajectory slope based on annual data only"
            )

        return CompRow(
            ticker=m["ticker"],
            name=ci.name or m["name"],
            role=role,
            currency=ci.currency,
            reporting_frequency=ci.reporting_frequency,
            fiscal_year_end_month=ci.fiscal_year_end_month,
            metrics=metrics,
            notes=notes,
        )

    # ---- Siemens Energy Grid Tech segment row ----------------------------

    def _build_grid_tech_segment_row(self, rows: list[CompRow]) -> CompRow:
        """Try to load FY2024 + FY2025_Q2 segment YAMLs; fall back to placeholder."""
        retrieved_at = datetime.now(timezone.utc)

        # Try to load whatever is available — newest first.
        available = list_available_periods()
        loaded: list[SegmentFinancials] = []
        for period in sorted(available, reverse=True):
            try:
                loaded.append(load_segment_data_manual(period))
            except (FileNotFoundError, ValueError) as e:
                log.info("Segment YAML %s not yet filled: %s", period, e)

        # Compose Grid Tech segment metrics
        metrics: dict[str, CompCell] = {}

        if not loaded:
            # No segment data available — emit a placeholder row.
            note = "data pending — fill data/raw/siemens_energy/segments_*.yaml"
            for k in (
                "ev_sales", "ev_ebitda", "pe_ttm", "pe_fwd", "pb",
                "fcf_yield", "rev_yoy", "rev_cagr_3y", "gross_margin",
                "ebitda_margin", "ebit_margin", "net_margin", "roe",
                "net_debt_ebitda", "ebit_margin_trajectory_slope",
                "ebitda_margin_trajectory_slope", "price",
                "pct_1m", "pct_3m", "pct_1y", "pct_ytd",
                "market_cap_native", "market_cap_usd",
            ):
                metrics[k] = CompCell(
                    value=None, source="manual_yaml", retrieved_at=retrieved_at,
                    note=note, flag="DATA_PENDING",
                )
            metrics["adjusted_ebita_margin"] = CompCell(
                value=None, note=note, flag="DATA_PENDING",
                source="manual_yaml", retrieved_at=retrieved_at,
            )

            return CompRow(
                ticker="ENR.DE:GridTech",
                name="Siemens Energy — Grid Technologies (segment)",
                role="target_segment",
                currency="EUR",
                reporting_frequency="quarterly",
                fiscal_year_end_month=9,
                metrics=metrics,
                notes=[note],
            )

        # We have at least one period — extract Grid Tech metrics.
        latest = loaded[0]
        gt = latest.grid_technologies

        # EV-based multiples are not segment-allocable.
        not_allocable = "n/a — segment EV not allocable"
        metrics["ev_sales"] = CompCell(
            value=None, note=not_allocable, source="manual_yaml",
            retrieved_at=retrieved_at,
        )
        metrics["ev_ebitda"] = CompCell(
            value=None, note=not_allocable, source="manual_yaml",
            retrieved_at=retrieved_at,
        )
        metrics["pe_ttm"] = CompCell(
            value=None, note=not_allocable, source="manual_yaml",
            retrieved_at=retrieved_at,
        )
        metrics["pe_fwd"] = CompCell(
            value=None, note=not_allocable, source="manual_yaml",
            retrieved_at=retrieved_at,
        )
        metrics["pb"] = CompCell(
            value=None, note=not_allocable, source="manual_yaml",
            retrieved_at=retrieved_at,
        )

        # Operating metrics ARE segment-meaningful.
        metrics["adjusted_ebita_margin"] = CompCell(
            value=gt.adjusted_ebita_margin,
            source="manual_yaml", retrieved_at=retrieved_at,
            source_url=latest.source_url,
        )
        metrics["ebit_margin"] = CompCell(
            value=gt.adjusted_ebita_margin,  # adjusted EBITA is closest to EBIT for the segment
            source="manual_yaml", retrieved_at=retrieved_at,
            source_url=latest.source_url,
            note="adjusted EBITA (Siemens Energy disclosure metric)",
        )
        metrics["ebitda_margin"] = CompCell(
            value=None,
            note="EBITDA not disclosed at segment level; see EBIT margin (adjusted EBITA)",
            source="manual_yaml", retrieved_at=retrieved_at,
        )

        if gt.book_to_bill is not None:
            metrics["book_to_bill"] = CompCell(
                value=gt.book_to_bill,
                source="manual_yaml", retrieved_at=retrieved_at,
                source_url=latest.source_url,
            )

        # Compute YoY revenue growth using the next-most-recent annual period
        # if it is available; otherwise leave blank.
        prior_annual = next(
            (s for s in loaded if s.fiscal_year < latest.fiscal_year and s.quarter is None),
            None,
        )
        if prior_annual and prior_annual.grid_technologies.revenue:
            yoy = gt.revenue / prior_annual.grid_technologies.revenue - 1 if gt.revenue else None
            metrics["rev_yoy"] = CompCell(
                value=yoy, source="manual_yaml", retrieved_at=retrieved_at,
                source_url=latest.source_url,
            )

        # Trajectory slope on adjusted_ebita_margin if we have ≥3 annual periods
        annual_periods = [s for s in loaded if s.quarter is None]
        if len(annual_periods) >= 3:
            xs = [s.fiscal_year for s in annual_periods]
            ys = [s.grid_technologies.adjusted_ebita_margin for s in annual_periods]
            if all(y is not None for y in ys):
                slope = _linear_slope(xs, ys)
                metrics["ebit_margin_trajectory_slope"] = CompCell(
                    value=slope, source="manual_yaml", retrieved_at=retrieved_at,
                    note="adjusted EBITA margin slope, pp/yr",
                )

        notes = [
            "Siemens Energy Grid Technologies segment, manually transcribed",
            f"latest period: {latest.fiscal_period}",
            "EV-based multiples not segment-allocable; consolidated row above is for context only",
        ]

        return CompRow(
            ticker="ENR.DE:GridTech",
            name="Siemens Energy — Grid Technologies (segment)",
            role="target_segment",
            currency=latest.currency,
            reporting_frequency="quarterly",
            fiscal_year_end_month=9,
            metrics=metrics,
            notes=notes,
        )

    # ---- Premium-to-peer-median ----------------------------------------

    def _populate_premium_to_median(self, rows: list[CompRow], target: str) -> None:
        """Add `<metric>_premium_to_median` cells for selected valuation metrics."""
        keys_to_compare = ("ev_sales", "ev_ebitda", "pe_fwd", "ebit_margin", "ebitda_margin")
        for k in keys_to_compare:
            peer_values = [
                r.metrics.get(k).value
                for r in rows
                if r.role == "peer"
                and r.metrics.get(k)
                and r.metrics.get(k).value is not None
            ]
            if len(peer_values) < 3:
                continue
            median = statistics.median(peer_values)
            if median == 0:
                continue
            for r in rows:
                cell = r.metrics.get(k)
                if cell and cell.value is not None:
                    pct = (cell.value / median) - 1
                    note = f"vs peer median {median:.2f}"
                    r.metrics[f"{k}_premium_to_median"] = CompCell(
                        value=pct, source=cell.source,
                        retrieved_at=cell.retrieved_at, note=note,
                    )

    # ---- FX -----------------------------------------------------------

    def _record_fx(self, currency: str) -> None:
        if currency in self.fx_rates_usd:
            return
        # Try to fetch from yfinance via XYZUSD=X
        if currency == "USD":
            self.fx_rates_usd["USD"] = 1.00
            return
        try:
            pair = f"{currency}USD=X"
            quote = self.yf.get_quote(pair)
            self.fx_rates_usd[currency] = quote.price
            log.info("FX %s=%.5f USD (yfinance)", currency, quote.price)
        except DataSourceError:
            fallback = _FX_FALLBACK.get(currency)
            if fallback is not None:
                self.fx_rates_usd[currency] = fallback
                log.warning("FX %s=%.5f USD (fallback)", currency, fallback)


# ---------------------------------------------------------------------------
# Static helpers


def _load_basket_yaml(basket_name: str) -> dict:
    path = _UNIVERSES_DIR / f"{basket_name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Basket YAML not found: {path}")
    return yaml.safe_load(path.read_text())


def _annual_margin_slope(periods: list[FinancialPeriod], field: str) -> Optional[float]:
    """
    Linear-regression slope (pp / year) of (numerator / revenue) over the
    last 4 annual periods.
    """
    if len(periods) < 3:
        return None
    sample = periods[:4]  # newest first
    xs: list[float] = []
    ys: list[float] = []
    for p in sample:
        rev = p.revenue
        num = getattr(p, field, None)
        if not rev or rev <= 0 or num is None:
            continue
        xs.append(p.period_end.year)
        ys.append(num / rev)
    if len(xs) < 3:
        return None
    return _linear_slope(xs, ys)


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den != 0 else 0.0


def _cross_market_caveats(basket: dict) -> dict:
    """Build the structured cross-market caveats object for this basket."""
    accounting_set = {m.get("accounting", "CAS" if m["currency"] == "CNY" else "?") for m in basket["members"]}
    freq_set = {m.get("reporting_frequency", "quarterly") for m in basket["members"]}
    fy_end_set = {m.get("fiscal_year_end_month", 12) for m in basket["members"]}
    listing_set = {m.get("primary_listing", "?") for m in basket["members"]}

    return {
        "accounting_standards": sorted(accounting_set),
        "reporting_frequencies": sorted(freq_set),
        "fiscal_year_end_months": sorted(int(m) for m in fy_end_set if m is not None),
        "primary_listings": sorted(listing_set),
    }

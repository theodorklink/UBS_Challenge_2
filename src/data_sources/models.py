"""
Pydantic models for data-source returns.

Every model carries provenance fields (source, retrieved_at, source_url).
Currency is recorded as the native currency of the source — conversion
happens later in the comp-table layer with the FX rate stored explicitly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- Provenance mixin -----------------------------------------------


class Provenance(BaseModel):
    """Fields that every data-source return carries."""

    model_config = ConfigDict(extra="allow")

    source: str = Field(..., description="Adapter name, e.g. 'yfinance', 'akshare'")
    retrieved_at: datetime
    source_url: Optional[str] = None
    source_page: Optional[int] = None
    cross_validation_status: Literal["match", "mismatch", "unavailable", "not_checked"] = "not_checked"
    cross_validation_note: Optional[str] = None


# ---------- Company info ---------------------------------------------------


class CompanyInfo(Provenance):
    ticker: str
    name: str
    exchange: str
    currency: str
    country: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[float] = None        # native currency
    shares_outstanding: Optional[float] = None
    fiscal_year_end_month: Optional[int] = None
    reporting_frequency: Literal["annual", "semi_annual", "quarterly", "unknown"] = "unknown"


# ---------- Quote ----------------------------------------------------------


class Quote(Provenance):
    ticker: str
    price: Optional[float] = None
    currency: str
    pct_1d: Optional[float] = None
    pct_1m: Optional[float] = None
    pct_3m: Optional[float] = None
    pct_1y: Optional[float] = None
    pct_ytd: Optional[float] = None


# ---------- Financial statements ------------------------------------------


class FinancialPeriod(BaseModel):
    """One row of an income statement / balance sheet / cash flow."""

    period_end: datetime
    period_type: Literal["annual", "semi_annual", "quarterly"]
    currency: str

    # Income statement (raw, native currency)
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None      # EBIT
    ebitda: Optional[float] = None
    net_income: Optional[float] = None

    # Balance sheet
    total_assets: Optional[float] = None
    total_debt: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    minority_interest: Optional[float] = None
    preferred_equity: Optional[float] = None
    total_equity: Optional[float] = None

    # Cash flow
    operating_cash_flow: Optional[float] = None
    capex: Optional[float] = None
    free_cash_flow: Optional[float] = None

    # Flags
    is_estimated: bool = False  # True if values are derived rather than reported


class Financials(Provenance):
    ticker: str
    currency: str
    annual: list[FinancialPeriod] = []
    quarterly: list[FinancialPeriod] = []


# ---------- Estimates ------------------------------------------------------


class Estimates(Provenance):
    ticker: str
    currency: str
    coverage: Literal["full", "partial", "insufficient"] = "insufficient"
    fy1_revenue_consensus: Optional[float] = None
    fy2_revenue_consensus: Optional[float] = None
    fy1_eps_consensus: Optional[float] = None
    fy2_eps_consensus: Optional[float] = None
    n_analysts: Optional[int] = None


# ---------- Price history --------------------------------------------------


class PricePoint(BaseModel):
    date: datetime
    close: float
    volume: Optional[float] = None


class PriceHistory(Provenance):
    ticker: str
    currency: str
    points: list[PricePoint] = []

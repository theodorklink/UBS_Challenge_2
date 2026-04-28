"""
yfinance adapter — Stage 1a.

Handles all tickers for which Yahoo Finance has data: Western
(ENR.DE, GEV, SU.PA, 6501.T, ETN, 7011.T) AND A-share (002028.SZ,
600406.SS, 000400.SZ, 600312.SS, 600089.SS, 601179.SS).

Note: yfinance uses .SS for Shanghai (not .SH). The unified router
normalizes between the two forms.

Caches every successful response under data/raw/yfinance/.
Fails loudly on invalid tickers — never silently returns empty objects.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yfinance as yf  # type: ignore

from .models import (
    CompanyInfo,
    Estimates,
    FinancialPeriod,
    Financials,
    PriceHistory,
    PricePoint,
    Quote,
)

log = logging.getLogger(__name__)


class DataSourceError(RuntimeError):
    pass


_CACHE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "yfinance"
_CACHE_ROOT.mkdir(parents=True, exist_ok=True)


def _cache_path(ticker: str, method: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe = ticker.replace(".", "_").replace("/", "_")
    return _CACHE_ROOT / f"{safe}_{method}_{today}.json"


def _safe_float(value: Any) -> Optional[float]:
    """Convert yfinance value to float; return None for NaN / missing."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


# --------------------------------------------------------------------------


class YFinanceAdapter:
    """yfinance wrapper with caching, provenance, and loud failure modes."""

    SOURCE = "yfinance"

    # ---- Public API --------------------------------------------------

    def verify_ticker(self, ticker: str) -> bool:
        """Return True if yfinance recognizes the ticker."""
        try:
            t = yf.Ticker(ticker)
            info = t.info
            return bool(info) and "symbol" in info
        except Exception:  # noqa: BLE001
            return False

    def get_company_info(self, ticker: str) -> CompanyInfo:
        cache = _cache_path(ticker, "info")
        if cache.exists():
            log.info("yfinance.info served from cache: %s", ticker)
            data = json.loads(cache.read_text())
            return CompanyInfo(**data)

        log.info("yfinance.info fetching: %s", ticker)
        try:
            info: dict = yf.Ticker(ticker).info
        except Exception as e:  # noqa: BLE001
            raise DataSourceError(f"yfinance failed for {ticker}: {e}") from e

        if not info or "symbol" not in info:
            raise DataSourceError(f"yfinance returned empty info for {ticker}")

        ci = CompanyInfo(
            source=self.SOURCE,
            retrieved_at=datetime.now(timezone.utc),
            source_url=f"https://finance.yahoo.com/quote/{ticker}",
            ticker=info.get("symbol", ticker),
            name=info.get("longName") or info.get("shortName") or ticker,
            exchange=info.get("exchange") or info.get("fullExchangeName") or "?",
            currency=info.get("currency") or "?",
            country=info.get("country"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            market_cap=_safe_float(info.get("marketCap")),
            shares_outstanding=_safe_float(info.get("sharesOutstanding")),
            fiscal_year_end_month=self._fiscal_year_end_month(info),
            reporting_frequency=self._reporting_frequency(ticker),
        )
        cache.write_text(ci.model_dump_json(indent=2))
        return ci

    def get_quote(self, ticker: str) -> Quote:
        cache = _cache_path(ticker, "quote")
        if cache.exists():
            return Quote(**json.loads(cache.read_text()))

        log.info("yfinance.quote fetching: %s", ticker)
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1y", auto_adjust=False)
            info = t.info
        except Exception as e:  # noqa: BLE001
            raise DataSourceError(f"yfinance quote failed for {ticker}: {e}") from e

        if hist.empty:
            raise DataSourceError(f"yfinance returned no price history for {ticker}")

        # Drop timezone — it differs per exchange and isn't relevant here.
        close = hist["Close"].copy()
        if close.index.tz is not None:
            close.index = close.index.tz_localize(None)

        last_price = float(close.iloc[-1])

        def pct_back(n_days: int) -> Optional[float]:
            if len(close) <= n_days:
                return None
            past = close.iloc[-n_days - 1]
            if past == 0:
                return None
            return (last_price - past) / past

        # YTD
        year_start = datetime(close.index[-1].year, 1, 1)
        ytd_subset = close[close.index >= year_start]
        pct_ytd: Optional[float] = None
        if not ytd_subset.empty and ytd_subset.iloc[0] != 0:
            pct_ytd = (last_price - float(ytd_subset.iloc[0])) / float(ytd_subset.iloc[0])

        q = Quote(
            source=self.SOURCE,
            retrieved_at=datetime.now(timezone.utc),
            source_url=f"https://finance.yahoo.com/quote/{ticker}",
            ticker=ticker,
            price=last_price,
            currency=info.get("currency") or "?",
            pct_1d=pct_back(1),
            pct_1m=pct_back(21),
            pct_3m=pct_back(63),
            pct_1y=pct_back(252),
            pct_ytd=pct_ytd,
        )
        cache.write_text(q.model_dump_json(indent=2))
        return q

    def get_financials(self, ticker: str) -> Financials:
        cache = _cache_path(ticker, "financials")
        if cache.exists():
            return Financials(**json.loads(cache.read_text()))

        log.info("yfinance.financials fetching: %s", ticker)
        try:
            t = yf.Ticker(ticker)
            ann_is = t.income_stmt
            ann_bs = t.balance_sheet
            ann_cf = t.cash_flow
            qtr_is = t.quarterly_income_stmt
            qtr_bs = t.quarterly_balance_sheet
            qtr_cf = t.quarterly_cash_flow
            currency = (t.info or {}).get("currency") or "?"
        except Exception as e:  # noqa: BLE001
            raise DataSourceError(f"yfinance financials failed for {ticker}: {e}") from e

        annual = self._merge_statements(ann_is, ann_bs, ann_cf, currency, "annual")
        quarterly = self._merge_statements(qtr_is, qtr_bs, qtr_cf, currency, "quarterly")

        f = Financials(
            source=self.SOURCE,
            retrieved_at=datetime.now(timezone.utc),
            source_url=f"https://finance.yahoo.com/quote/{ticker}/financials",
            ticker=ticker,
            currency=currency,
            annual=annual,
            quarterly=quarterly,
        )
        cache.write_text(f.model_dump_json(indent=2))
        return f

    def get_estimates(self, ticker: str) -> Estimates:
        cache = _cache_path(ticker, "estimates")
        if cache.exists():
            return Estimates(**json.loads(cache.read_text()))

        log.info("yfinance.estimates fetching: %s", ticker)
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            currency = info.get("currency") or "?"

            est = Estimates(
                source=self.SOURCE,
                retrieved_at=datetime.now(timezone.utc),
                source_url=f"https://finance.yahoo.com/quote/{ticker}/analysis",
                ticker=ticker,
                currency=currency,
                fy1_revenue_consensus=_safe_float(info.get("revenueEstimateAvg")),
                fy2_revenue_consensus=None,  # yfinance .info often only has FY1
                fy1_eps_consensus=_safe_float(info.get("forwardEps")),
                fy2_eps_consensus=None,
                n_analysts=info.get("numberOfAnalystOpinions"),
            )

            # Decide coverage label.
            populated = sum(
                1 for v in (
                    est.fy1_revenue_consensus, est.fy1_eps_consensus,
                    est.fy2_revenue_consensus, est.fy2_eps_consensus,
                ) if v is not None
            )
            if populated >= 3:
                est.coverage = "full"
            elif populated >= 1:
                est.coverage = "partial"
            else:
                est.coverage = "insufficient"

        except Exception as e:  # noqa: BLE001
            log.warning("yfinance estimates failed for %s: %s — returning insufficient", ticker, e)
            est = Estimates(
                source=self.SOURCE,
                retrieved_at=datetime.now(timezone.utc),
                ticker=ticker,
                currency="?",
                coverage="insufficient",
            )

        cache.write_text(est.model_dump_json(indent=2))
        return est

    def get_price_history(self, ticker: str, years: int = 3) -> PriceHistory:
        cache = _cache_path(ticker, f"prices_{years}y")
        if cache.exists():
            return PriceHistory(**json.loads(cache.read_text()))

        log.info("yfinance.history fetching: %s (%dy)", ticker, years)
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period=f"{years}y", auto_adjust=False)
            currency = (t.info or {}).get("currency") or "?"
        except Exception as e:  # noqa: BLE001
            raise DataSourceError(f"yfinance history failed for {ticker}: {e}") from e

        if hist.empty:
            raise DataSourceError(f"yfinance returned no history for {ticker}")

        points = [
            PricePoint(
                date=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                close=float(row["Close"]),
                volume=float(row["Volume"]) if not math.isnan(row["Volume"]) else None,
            )
            for ts, row in hist.iterrows()
        ]

        ph = PriceHistory(
            source=self.SOURCE,
            retrieved_at=datetime.now(timezone.utc),
            source_url=f"https://finance.yahoo.com/quote/{ticker}/history",
            ticker=ticker,
            currency=currency,
            points=points,
        )
        cache.write_text(ph.model_dump_json(indent=2))
        return ph

    # ---- Helpers ------------------------------------------------------

    def _fiscal_year_end_month(self, info: dict) -> Optional[int]:
        """Best-effort fiscal year-end month inference."""
        # yfinance 'lastFiscalYearEnd' is a unix timestamp; otherwise None.
        ts = info.get("lastFiscalYearEnd")
        if ts:
            try:
                return datetime.fromtimestamp(ts, tz=timezone.utc).month
            except Exception:  # noqa: BLE001
                pass
        return None

    def _reporting_frequency(self, ticker: str) -> str:
        """Heuristic reporting frequency inference."""
        # Sieyuan reports semi-annually. Other A-shares are quarterly.
        # We hardcode the known case for Sieyuan; default quarterly.
        if ticker.upper().startswith("002028"):
            return "semi_annual"
        return "quarterly"

    def _merge_statements(
        self, is_df, bs_df, cf_df, currency: str, period_type: str
    ) -> list[FinancialPeriod]:
        """
        yfinance returns each statement as a DataFrame indexed by line item,
        with columns being period-end dates. Pivot into per-period rows.
        """
        if is_df is None or is_df.empty:
            return []

        periods: dict[datetime, FinancialPeriod] = {}

        def _push(df, mapping: dict[str, str]) -> None:
            if df is None or df.empty:
                return
            for col in df.columns:
                pe = col.to_pydatetime() if hasattr(col, "to_pydatetime") else col
                period = periods.setdefault(
                    pe,
                    FinancialPeriod(period_end=pe, period_type=period_type, currency=currency),
                )
                for src_label, model_field in mapping.items():
                    if src_label in df.index:
                        v = _safe_float(df.loc[src_label, col])
                        if v is not None:
                            setattr(period, model_field, v)

        _push(is_df, {
            "Total Revenue": "revenue",
            "Gross Profit": "gross_profit",
            "Operating Income": "operating_income",
            "EBITDA": "ebitda",
            "Net Income": "net_income",
        })
        _push(bs_df, {
            "Total Assets": "total_assets",
            "Total Debt": "total_debt",
            "Cash And Cash Equivalents": "cash_and_equivalents",
            "Minority Interest": "minority_interest",
            "Preferred Stock Equity": "preferred_equity",
            "Stockholders Equity": "total_equity",
        })
        _push(cf_df, {
            "Operating Cash Flow": "operating_cash_flow",
            "Capital Expenditure": "capex",
            "Free Cash Flow": "free_cash_flow",
        })

        return sorted(periods.values(), key=lambda p: p.period_end, reverse=True)

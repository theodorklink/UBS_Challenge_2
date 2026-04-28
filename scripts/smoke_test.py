"""
Smoke test: pull data for both targets and one peer of each basket via
yfinance, print a summary. This is the first end-to-end sanity check
before building the comp table.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Make the repo root importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env", override=True)
except Exception:
    pass

from src.data_sources.yfinance_adapter import YFinanceAdapter, DataSourceError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


TICKERS = [
    ("ENR.DE", "Siemens Energy (LONG target)"),
    ("002028.SZ", "Sieyuan Electric (SHORT target)"),
    ("GEV", "GE Vernova (peer in global_power)"),
    ("600406.SS", "NARI Technology (peer in china_td)"),
]


def main() -> int:
    adapter = YFinanceAdapter()
    print(f"\n{'TICKER':<14} {'NAME':<35} {'CCY':<5} {'PRICE':>10} {'MCAP_BN':>10} {'FREQ':<13} {'STATUS'}")
    print("-" * 110)

    failures = 0
    for ticker, label in TICKERS:
        try:
            ci = adapter.get_company_info(ticker)
            q = adapter.get_quote(ticker)
            mcap_bn = (ci.market_cap or 0) / 1e9
            print(
                f"{ticker:<14} {ci.name[:33]:<35} {ci.currency:<5} "
                f"{q.price:>10.2f} {mcap_bn:>10.2f} {ci.reporting_frequency:<13} OK"
            )
        except DataSourceError as e:
            print(f"{ticker:<14} {label[:33]:<35} {'-':<5} {'-':>10} {'-':>10} {'-':<13} FAIL: {e}")
            failures += 1
        except Exception as e:  # noqa: BLE001
            print(f"{ticker:<14} {label[:33]:<35} ERROR: {type(e).__name__}: {e}")
            failures += 1

    print("\n=== Financials sanity check (ENR.DE last 4 annual periods) ===")
    try:
        f = adapter.get_financials("ENR.DE")
        for p in f.annual[:4]:
            rev_bn = (p.revenue or 0) / 1e9
            ebit = (p.operating_income or 0) / 1e9
            print(f"  {p.period_end.date()}  revenue={rev_bn:>7.2f}bn  EBIT={ebit:>6.2f}bn")
    except DataSourceError as e:
        print(f"  FAIL: {e}")
        failures += 1

    print("\n=== Financials sanity check (002028.SZ last 4 annual periods) ===")
    try:
        f = adapter.get_financials("002028.SZ")
        for p in f.annual[:4]:
            rev_bn = (p.revenue or 0) / 1e9
            ebit = (p.operating_income or 0) / 1e9
            print(f"  {p.period_end.date()}  revenue={rev_bn:>7.2f}bn  EBIT={ebit:>6.2f}bn")
    except DataSourceError as e:
        print(f"  FAIL: {e}")
        failures += 1

    print("\n=== Estimates coverage (forward-looking) ===")
    for ticker, _ in TICKERS:
        try:
            est = adapter.get_estimates(ticker)
            print(f"  {ticker:<14}  coverage={est.coverage:<13} fy1_rev={est.fy1_revenue_consensus}  fy1_eps={est.fy1_eps_consensus}")
        except DataSourceError as e:
            print(f"  {ticker:<14}  FAIL: {e}")
            failures += 1

    print(f"\n{'='*60}")
    if failures:
        print(f"FAILURES: {failures}")
        return 1
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

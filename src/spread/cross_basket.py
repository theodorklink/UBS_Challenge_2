"""
Cross-basket spread analyzer — Stage 4.

Consumes two CompTables (china_td, global_power) and produces the
pair-trade analytical artifact:
  - Direct pair: Sieyuan vs ENR.DE consolidated AND Grid Tech segment
  - Basket-vs-basket median spread (excludes targets — isolates structural
    A-share vs European multiple differential)
  - Relative positioning (target percentile within own basket; RICH /
    NEUTRAL / CHEAP labels using IQR thresholds)
  - Trajectory asymmetry (margin slope target vs basket median slope)
  - Valuation-reset sensitivity (implied % move if multiple reverts to
    basket median forward P/E)
  - Policy exposure overlay (loaded from YAML, surfaced; not scored)

Pure data — no LLM, no thesis generation.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..comparables.models import CompRow, CompTable

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_POLICY_YAML = _REPO_ROOT / "data" / "policy" / "exposure_2026.yaml"


class SpreadCell(BaseModel):
    """One cell in the spread output. Carries provenance hint."""

    value: Optional[float] = None
    label: Optional[str] = None
    note: Optional[str] = None


class RelativePositioning(BaseModel):
    metric: str
    target_value: Optional[float]
    basket_median_excl_target: Optional[float]
    basket_iqr_excl_target: Optional[float]
    target_position: Literal["RICH", "NEUTRAL", "CHEAP", "n/a"] = "n/a"
    percentile_within_basket: Optional[float] = None  # 0..1


class DirectPairLine(BaseModel):
    metric: str
    sieyuan_value: Optional[float]
    enr_consolidated_value: Optional[float]
    enr_grid_segment_value: Optional[float]
    raw_spread_consolidated: Optional[float]
    raw_spread_grid_segment: Optional[float]


class ResetSensitivity(BaseModel):
    target_label: str
    target_metric_value: Optional[float]
    basket_median_excl_target: Optional[float]
    implied_pct_move_to_median: Optional[float]
    note: Optional[str] = None


class PolicyEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    policy: str
    target_ticker: str
    exposure_score: Optional[float] = None
    rationale: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[str] = None


class SpreadAnalysis(BaseModel):
    model_config = ConfigDict(extra="allow")
    generated_at: datetime
    direct_pair: list[DirectPairLine]
    basket_vs_basket: list[DirectPairLine]
    relative_positioning_sieyuan: list[RelativePositioning]
    relative_positioning_siemens: list[RelativePositioning]
    trajectory_asymmetry: dict[str, dict[str, Optional[float]]]
    valuation_reset_sensitivity: list[ResetSensitivity]
    policy_exposure: list[PolicyEntry]
    cross_market_caveats: dict[str, Any]


# ---------------------------------------------------------------------------


_VALUATION_METRICS = ("ev_sales", "ev_ebitda", "pe_fwd", "pe_ttm", "fcf_yield")
_OPERATING_METRICS = ("ebit_margin", "ebitda_margin", "rev_yoy",
                      "rev_cagr_3y", "roe", "net_debt_ebitda")
_DIRECT_PAIR_METRICS = (
    "ev_ebitda", "pe_fwd", "ebit_margin", "ebitda_margin",
    "rev_yoy", "rev_cagr_3y", "roe", "net_debt_ebitda", "fcf_yield",
)


class CrossBasketAnalyzer:
    def __init__(self, china_table: CompTable, global_table: CompTable):
        self.china = china_table
        self.global_ = global_table

    # ---- Public API ------------------------------------------------

    def analyze(self) -> SpreadAnalysis:
        sieyuan = self._target_row(self.china)
        enr = self._target_row(self.global_)
        enr_grid = self._segment_row(self.global_)

        china_peers = [r for r in self.china.rows if r.role == "peer"]
        global_peers = [r for r in self.global_.rows if r.role == "peer"]

        # 1. Direct pair
        direct_pair = self._direct_pair_lines(sieyuan, enr, enr_grid)

        # 2. Basket-vs-basket median spread
        bvb = self._basket_vs_basket(china_peers, global_peers)

        # 3. Relative positioning
        rp_sieyuan = [
            self._relative_positioning(sieyuan, china_peers, m)
            for m in (*_VALUATION_METRICS, *_OPERATING_METRICS)
        ]
        rp_siemens = [
            self._relative_positioning(enr, global_peers, m)
            for m in (*_VALUATION_METRICS, *_OPERATING_METRICS)
        ]

        # 4. Trajectory asymmetry
        trajectory = self._trajectory_asymmetry(sieyuan, china_peers, enr, global_peers)

        # 5. Valuation-reset sensitivity (forward P/E based)
        reset = self._valuation_reset(sieyuan, china_peers, enr, enr_grid, global_peers)

        # 6. Policy overlay
        policy = self._load_policy()

        # 7. Combined cross-market caveats
        cross_caveats = self._combined_cross_market_caveats()

        return SpreadAnalysis(
            generated_at=datetime.now(timezone.utc),
            direct_pair=direct_pair,
            basket_vs_basket=bvb,
            relative_positioning_sieyuan=rp_sieyuan,
            relative_positioning_siemens=rp_siemens,
            trajectory_asymmetry=trajectory,
            valuation_reset_sensitivity=reset,
            policy_exposure=policy,
            cross_market_caveats=cross_caveats,
        )

    # ---- Helpers ---------------------------------------------------

    def _target_row(self, table: CompTable) -> CompRow:
        for r in table.rows:
            if r.role == "target":
                return r
        raise ValueError(f"no target row in basket {table.basket}")

    def _segment_row(self, table: CompTable) -> Optional[CompRow]:
        for r in table.rows:
            if r.role == "target_segment":
                return r
        return None

    def _val(self, row: Optional[CompRow], metric: str) -> Optional[float]:
        if row is None or metric not in row.metrics:
            return None
        return row.metrics[metric].value

    def _direct_pair_lines(
        self, sieyuan: CompRow, enr: CompRow, enr_grid: Optional[CompRow]
    ) -> list[DirectPairLine]:
        out: list[DirectPairLine] = []
        for m in _DIRECT_PAIR_METRICS:
            sv = self._val(sieyuan, m)
            ev = self._val(enr, m)
            gv = self._val(enr_grid, m) if enr_grid else None
            spread_consolidated = (sv - ev) if (sv is not None and ev is not None) else None
            spread_grid = (sv - gv) if (sv is not None and gv is not None) else None
            out.append(DirectPairLine(
                metric=m,
                sieyuan_value=sv,
                enr_consolidated_value=ev,
                enr_grid_segment_value=gv,
                raw_spread_consolidated=spread_consolidated,
                raw_spread_grid_segment=spread_grid,
            ))
        return out

    def _basket_vs_basket(
        self, china_peers: list[CompRow], global_peers: list[CompRow]
    ) -> list[DirectPairLine]:
        out: list[DirectPairLine] = []
        for m in _DIRECT_PAIR_METRICS:
            cm = _median_of(china_peers, m)
            gm = _median_of(global_peers, m)
            spread = (cm - gm) if (cm is not None and gm is not None) else None
            out.append(DirectPairLine(
                metric=m,
                sieyuan_value=cm,                          # reusing field name as china median
                enr_consolidated_value=gm,                 # reusing field name as global median
                enr_grid_segment_value=None,
                raw_spread_consolidated=spread,
                raw_spread_grid_segment=None,
            ))
        return out

    def _relative_positioning(
        self, target: CompRow, peers: list[CompRow], metric: str
    ) -> RelativePositioning:
        peer_values = [
            r.metrics[metric].value for r in peers
            if metric in r.metrics and r.metrics[metric].value is not None
        ]
        target_value = self._val(target, metric)

        if not peer_values or target_value is None:
            return RelativePositioning(
                metric=metric,
                target_value=target_value,
                basket_median_excl_target=None,
                basket_iqr_excl_target=None,
            )

        median = statistics.median(peer_values)
        try:
            q = statistics.quantiles(peer_values, n=4)
            iqr = q[2] - q[0]
        except statistics.StatisticsError:
            iqr = None

        position: Literal["RICH", "NEUTRAL", "CHEAP", "n/a"] = "n/a"
        if iqr is not None and iqr > 0:
            if target_value > median + 0.5 * iqr:
                position = "RICH"
            elif target_value < median - 0.5 * iqr:
                position = "CHEAP"
            else:
                position = "NEUTRAL"

        # For metrics where higher = bullish (ebit_margin, roe), invert
        # the rich/cheap labels so "RICH" always means "expensive / less
        # attractive" relative to peers.
        if metric in ("ebit_margin", "ebitda_margin", "roe", "rev_yoy", "rev_cagr_3y"):
            # Higher is better → label remains as positioning of value:
            # being above median is "high quality", we keep RICH/CHEAP
            # naming consistent with valuation metrics. To avoid confusion
            # we add a note in the rendered output.
            pass

        # Percentile estimate (basic — count below + 0.5 if equal)
        below = sum(1 for v in peer_values if v < target_value)
        equal = sum(1 for v in peer_values if v == target_value)
        percentile = (below + 0.5 * equal) / len(peer_values) if peer_values else None

        return RelativePositioning(
            metric=metric,
            target_value=target_value,
            basket_median_excl_target=median,
            basket_iqr_excl_target=iqr,
            target_position=position,
            percentile_within_basket=percentile,
        )

    def _trajectory_asymmetry(
        self,
        sieyuan: CompRow,
        china_peers: list[CompRow],
        enr: CompRow,
        global_peers: list[CompRow],
    ) -> dict[str, dict[str, Optional[float]]]:
        out: dict[str, dict[str, Optional[float]]] = {}
        slope_metric = "ebit_margin_trajectory_slope"
        out["ebit_margin"] = {
            "sieyuan_slope": self._val(sieyuan, slope_metric),
            "china_basket_median_slope": _median_of(china_peers, slope_metric),
            "siemens_consolidated_slope": self._val(enr, slope_metric),
            "global_basket_median_slope": _median_of(global_peers, slope_metric),
        }
        return out

    def _valuation_reset(
        self,
        sieyuan: CompRow,
        china_peers: list[CompRow],
        enr: CompRow,
        enr_grid: Optional[CompRow],
        global_peers: list[CompRow],
    ) -> list[ResetSensitivity]:
        out: list[ResetSensitivity] = []

        # Sieyuan vs China basket forward P/E
        s_pe = self._val(sieyuan, "pe_fwd")
        china_med = _median_of(china_peers, "pe_fwd")
        sieyuan_move = None
        if s_pe and china_med and s_pe > 0:
            sieyuan_move = (china_med / s_pe) - 1
        out.append(ResetSensitivity(
            target_label="Sieyuan vs China T&D basket forward P/E",
            target_metric_value=s_pe,
            basket_median_excl_target=china_med,
            implied_pct_move_to_median=sieyuan_move,
            note="negative = downside if multiple reverts to basket median",
        ))

        # ENR consolidated vs global basket
        e_pe = self._val(enr, "pe_fwd")
        global_med = _median_of(global_peers, "pe_fwd")
        enr_move = None
        if e_pe and global_med and e_pe > 0:
            enr_move = (global_med / e_pe) - 1
        out.append(ResetSensitivity(
            target_label="Siemens Energy (consolidated) vs Global Power basket forward P/E",
            target_metric_value=e_pe,
            basket_median_excl_target=global_med,
            implied_pct_move_to_median=enr_move,
            note="negative = downside if multiple reverts",
        ))

        # ENR Grid Tech segment — if the segment YAML provided EBIT margin etc.
        # we substitute EBIT margin reset (since segment EV is not allocable).
        if enr_grid is not None and enr_grid.metrics.get("ebit_margin") and enr_grid.metrics["ebit_margin"].value is not None:
            seg_margin = enr_grid.metrics["ebit_margin"].value
            global_med_margin = _median_of(global_peers, "ebit_margin")
            margin_gap = None
            if seg_margin and global_med_margin:
                margin_gap = seg_margin - global_med_margin
            out.append(ResetSensitivity(
                target_label="Siemens Energy Grid Tech (segment) — EBIT margin vs Global basket median",
                target_metric_value=seg_margin,
                basket_median_excl_target=global_med_margin,
                implied_pct_move_to_median=margin_gap,
                note="absolute pp difference (positive = segment ahead of basket)",
            ))

        return out

    def _load_policy(self) -> list[PolicyEntry]:
        if not _POLICY_YAML.exists():
            return []
        raw = yaml.safe_load(_POLICY_YAML.read_text())
        out: list[PolicyEntry] = []
        for entry in raw.get("policies", []):
            # Skip placeholders (any TODO field)
            if any(isinstance(v, str) and v.strip().startswith("TODO") for v in entry.values()):
                continue
            try:
                out.append(PolicyEntry(**entry))
            except Exception as e:  # noqa: BLE001
                log.warning("Skipping malformed policy entry: %s — %s", entry, e)
        return out

    def _combined_cross_market_caveats(self) -> dict[str, Any]:
        cn = self.china.cross_market_caveats or {}
        gl = self.global_.cross_market_caveats or {}
        return {
            "china_basket": cn,
            "global_basket": gl,
            "key_asymmetries": [
                "Accounting standards: CAS (China) vs IFRS / USGAAP / JGAAP (global)",
                "Reporting frequency: Sieyuan semi-annual vs Siemens Energy quarterly",
                "Fiscal year-end: Dec (China) vs Sept (Siemens Energy) vs Mar (Japanese peers)",
                "Listing market: A-share with capital controls and Stock Connect dynamics vs European/US listings",
                "Western analyst coverage limited for A-share peers — forward consensus often unavailable",
            ],
        }


# ---------------------------------------------------------------------------


def _median_of(rows: list[CompRow], metric: str) -> Optional[float]:
    vals = [
        r.metrics[metric].value for r in rows
        if metric in r.metrics and r.metrics[metric].value is not None
    ]
    if not vals:
        return None
    return statistics.median(vals)

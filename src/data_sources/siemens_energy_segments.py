"""
Siemens Energy segment adapter — Stage 1c.

Two paths:
  - load_segment_data_manual(...)  : reads hand-filled YAML in
                                     data/raw/siemens_energy/.
  - load_segment_data_automated(...): pdfplumber-based PDF extraction
                                     (planned, not yet implemented).
  - validate_manual_against_automated(...): cross-check (planned).

For Phase 1 we ship the manual loader only. Once you fill in the
YAMLs, the comp-table layer can consume them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .models import Provenance

log = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SEGMENT_DIR = _REPO_ROOT / "data" / "raw" / "siemens_energy"


SEGMENT_NAMES = ("gas_services", "grid_technologies",
                 "transformation_of_industry", "siemens_gamesa")


class SegmentValues(BaseModel):
    """One segment's metrics for a single fiscal period."""

    model_config = ConfigDict(extra="allow")

    revenue: Optional[float] = None
    orders: Optional[float] = None
    book_to_bill: Optional[float] = None
    adjusted_ebita: Optional[float] = None
    adjusted_ebita_margin: Optional[float] = None
    free_cash_flow: Optional[float] = None
    employees: Optional[int] = None
    source_page: Optional[int] = None


class SegmentFinancials(Provenance):
    """Full segment-level financials for one Siemens Energy fiscal period."""

    fiscal_period: str
    fiscal_year: int
    quarter: Optional[int] = None
    period_start: datetime
    period_end: datetime
    currency: str
    unit: Literal["millions", "billions"]

    gas_services: SegmentValues
    grid_technologies: SegmentValues
    transformation_of_industry: SegmentValues
    siemens_gamesa: SegmentValues
    group_consolidated: SegmentValues

    notes: str = ""

    def verify(self) -> list[str]:
        """Return a list of validation issues. Empty list = OK."""
        issues: list[str] = []
        if self.source_url is None or self.source_url.startswith("TODO"):
            issues.append("source_url not filled")

        for name in SEGMENT_NAMES:
            seg: SegmentValues = getattr(self, name)
            if seg.source_page is None or (isinstance(seg.source_page, str) and
                                           seg.source_page.startswith("TODO")):
                issues.append(f"{name}: source_page missing")
            if seg.revenue is None:
                issues.append(f"{name}: revenue missing")
            if seg.adjusted_ebita is None:
                issues.append(f"{name}: adjusted_ebita missing")

        if self.group_consolidated.revenue is None:
            issues.append("group_consolidated: revenue missing")
        return issues


# --------------------------------------------------------------------------


def load_segment_data_manual(fiscal_period: str) -> SegmentFinancials:
    """
    Load a hand-filled YAML for one Siemens Energy fiscal period.

    Args:
      fiscal_period: e.g. "FY2024_annual", "FY2025_Q1", "FY2025_Q2".
        Must match a YAML basename in data/raw/siemens_energy/.

    Returns:
      SegmentFinancials with `source = 'manual_yaml'`.

    Raises:
      FileNotFoundError if YAML missing.
      ValueError if YAML still has 'TODO' placeholders for required fields.
    """
    path = _SEGMENT_DIR / f"segments_{fiscal_period}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Segment YAML not found: {path}")

    raw = yaml.safe_load(path.read_text())

    # Detect un-filled placeholders.
    todos = _find_todos(raw)
    if todos:
        raise ValueError(
            f"Segment YAML {path.name} still has TODO placeholders for: "
            + ", ".join(todos[:8])
            + (f" (and {len(todos) - 8} more)" if len(todos) > 8 else "")
        )

    sf = SegmentFinancials(
        source="manual_yaml",
        retrieved_at=datetime.now(timezone.utc),
        source_url=raw.get("source_document_url"),
        fiscal_period=raw["fiscal_period"],
        fiscal_year=raw["fiscal_year"],
        quarter=raw.get("quarter"),
        period_start=datetime.fromisoformat(raw["period_start"]),
        period_end=datetime.fromisoformat(raw["period_end"]),
        currency=raw["currency"],
        unit=raw["unit"],
        gas_services=SegmentValues(**raw["segments"]["gas_services"]),
        grid_technologies=SegmentValues(**raw["segments"]["grid_technologies"]),
        transformation_of_industry=SegmentValues(
            **raw["segments"]["transformation_of_industry"]
        ),
        siemens_gamesa=SegmentValues(**raw["segments"]["siemens_gamesa"]),
        group_consolidated=SegmentValues(**raw["group_consolidated"]),
        notes=raw.get("notes") or "",
    )

    issues = sf.verify()
    if issues:
        log.warning(
            "Segment YAML %s loaded but has open issues: %s",
            path.name, "; ".join(issues),
        )
    return sf


def list_available_periods() -> list[str]:
    """Return list of fiscal_period strings for which YAML files exist."""
    return sorted([
        p.stem.replace("segments_", "")
        for p in _SEGMENT_DIR.glob("segments_*.yaml")
    ])


# --------------------------------------------------------------------------
# Helpers


def _find_todos(obj, path: str = "") -> list[str]:
    """Recursively find string values that start with 'TODO'."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_find_todos(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_find_todos(v, f"{path}[{i}]"))
    elif isinstance(obj, str) and obj.strip().startswith("TODO"):
        out.append(path)
    return out


# --------------------------------------------------------------------------
# Stubs for Path B (pdfplumber automated extraction) — TODO Phase 2


def load_segment_data_automated(fiscal_period: str) -> SegmentFinancials:
    """Pdfplumber-based extraction. Not implemented in Phase 1."""
    raise NotImplementedError(
        "Automated PDF extraction will be added in Phase 2 as a "
        "cross-check against the manual YAML."
    )


def validate_manual_against_automated(fiscal_period: str) -> dict:
    """Cross-check the two paths. Not implemented in Phase 1."""
    raise NotImplementedError(
        "Cross-validation will be added in Phase 2 once the pdfplumber "
        "adapter exists."
    )

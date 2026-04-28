"""
Pydantic models for comp tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CompCell(BaseModel):
    """One cell of a comp-table row, with provenance and flags."""

    model_config = ConfigDict(extra="allow")

    value: Optional[float] = None
    source: str = "yfinance"
    retrieved_at: Optional[datetime] = None
    source_url: Optional[str] = None
    note: Optional[str] = None
    flag: Optional[Literal["OUTLIER_HIGH", "OUTLIER_LOW", "INCOMPLETE", "DATA_PENDING"]] = None


class CompRow(BaseModel):
    """One company in a comp-table basket."""

    model_config = ConfigDict(extra="allow")

    ticker: str
    name: str
    role: Literal["target", "peer", "target_segment"]
    currency: str
    reporting_frequency: str = "unknown"
    fiscal_year_end_month: Optional[int] = None

    # Free-form metric dict — keys are well-known constants documented in
    # comp_builder.METRIC_KEYS.
    metrics: dict[str, CompCell] = Field(default_factory=dict)

    notes: list[str] = Field(default_factory=list)


class CompTable(BaseModel):
    """Full comp table for one basket + target."""

    model_config = ConfigDict(extra="allow")

    basket: str
    target_ticker: str
    rows: list[CompRow]

    fx_rates_used: dict[str, float] = Field(default_factory=dict)
    cross_market_caveats: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
    verified: bool = False
    verification_notes: list[str] = Field(default_factory=list)

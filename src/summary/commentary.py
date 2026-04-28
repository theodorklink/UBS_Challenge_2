"""
Commentary drafter — Stage 5.

Consumes a SpreadAnalysis and asks Claude Sonnet 4.6 to produce a
sell-side-style observational commentary. STRICT safety perimeter:
forbidden tokens (long, short, buy, sell, target price, etc.) are
blocked by a deterministic postcheck. The output is ALWAYS marked
DRAFT — REQUIRES HUMAN REVIEW.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..spread.cross_basket import SpreadAnalysis

log = logging.getLogger(__name__)


FORBIDDEN_TOKENS = [
    r"\blong\b",
    r"\bshort\b",
    r"\bbuy\b",
    r"\bsell\b",
    r"\boverweight\b",
    r"\bunderweight\b",
    r"\boutperform\b",
    r"\bunderperform\b",
    r"\btarget price\b",
    r"\bfair value\b",
    r"\bwe recommend\b",
    r"\btrade idea\b",
    r"\bgo long\b",
    r"\bgo short\b",
]
_FORBIDDEN_RE = re.compile("|".join(FORBIDDEN_TOKENS), flags=re.IGNORECASE)


SYSTEM_PROMPT = """You are a senior sell-side equity research analyst writing OBSERVATIONAL commentary on a structured pair-trade comparison dataset for a student finance competition. The dataset compares two targets (Siemens Energy and Sieyuan Electric) across two regional peer baskets (Global Power, China T&D).

CRITICAL RULES:
- You write OBSERVATIONS based STRICTLY on the data provided.
- You do NOT recommend a long, short, buy, or sell position.
- You do NOT name a fair value, target price, intrinsic value or DCF input.
- You do NOT make predictions about future returns.
- You do NOT use the words: long, short, buy, sell, overweight, underweight, outperform, underperform, target price, fair value, we recommend, trade idea, go long, go short.
- You DO acknowledge cross-market asymmetry (A-share vs European listings) explicitly in any cross-market comparison.
- You DO frame valuation observations in basket-relative terms (rich/cheap relative to own peer basket) rather than direct cross-market comparisons.
- You DO surface trajectory asymmetries (margin slope, growth deceleration) where the data supports them.
- Sell-side register: factual, cautious, defensible. Use professional analyst language.

OUTPUT FORMAT — return ONLY valid JSON (no markdown fences, no preamble) with this schema:

{
  "one_line_summary": "string (≤ 30 words, observational on what the data SHOWS)",
  "direct_pair_observations": "string (2-3 paragraphs on Sieyuan vs Siemens Energy, with explicit cross-market caveats)",
  "basket_relative_positioning": "string (1 paragraph on where each target sits within its own basket — rich / neutral / cheap)",
  "trajectory_asymmetry_observations": "string (1 paragraph on operating-momentum asymmetries between the two targets, citing slope values from the data)",
  "valuation_reset_observations": "string (1 short paragraph on the asymmetric median-revert sensitivity, citing % values)",
  "data_flags": "string (anomalies, missing data, cross-validation mismatches, coverage gaps)",
  "limitations": "string (what this analysis CANNOT tell you — cycle position, macro / FX outlook, technological obsolescence, regulatory enforcement timing)"
}

Output JSON ONLY. No prose, no markdown fences."""


class Commentary(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: datetime
    model_used: str
    one_line_summary: str = ""
    direct_pair_observations: str = ""
    basket_relative_positioning: str = ""
    trajectory_asymmetry_observations: str = ""
    valuation_reset_observations: str = ""
    data_flags: str = ""
    limitations: str = ""
    draft_banner: str = (
        "*** DRAFT — REQUIRES HUMAN REVIEW. AI-GENERATED FROM SPREAD "
        "ANALYSIS DATA ONLY. NOT A TRADE RECOMMENDATION. ***"
    )
    forbidden_tokens_postcheck: str = "passed"


class CommentaryError(RuntimeError):
    pass


# ---------------------------------------------------------------------------


def draft(spread: SpreadAnalysis, model: Optional[str] = None) -> Commentary:
    """Call Anthropic Claude with the spread analysis and produce a Commentary."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise CommentaryError("ANTHROPIC_API_KEY is not set")

    model = model or os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    from anthropic import Anthropic  # type: ignore
    client = Anthropic(api_key=api_key)

    payload = json.loads(spread.model_dump_json())
    user_message = (
        "Here is the SpreadAnalysis JSON for the Sieyuan / Siemens Energy "
        "pair. Produce the observational commentary JSON now.\n\n"
        + json.dumps(payload, indent=2)
    )

    log.info("Calling Anthropic %s for commentary draft", model)
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    text = _strip_code_fences(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise CommentaryError(f"Commentary did not return valid JSON: {e}\nRaw: {text[:600]}")

    # Forbidden-token postcheck.
    # Strip legitimate analyst compounds first (sell-side / buy-side are
    # role names, not trade directions).
    combined = " ".join(str(v) for v in parsed.values() if isinstance(v, str))
    cleaned = re.sub(r"\bsell[- ]side\b", "", combined, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bbuy[- ]side\b", "", cleaned, flags=re.IGNORECASE)
    matches = _FORBIDDEN_RE.findall(cleaned)
    if matches:
        raise CommentaryError(
            f"Commentary contained forbidden tokens: {sorted(set(matches))!r}. "
            "Refusing to save."
        )

    return Commentary(
        generated_at=datetime.now(timezone.utc),
        model_used=model,
        one_line_summary=parsed.get("one_line_summary", ""),
        direct_pair_observations=parsed.get("direct_pair_observations", ""),
        basket_relative_positioning=parsed.get("basket_relative_positioning", ""),
        trajectory_asymmetry_observations=parsed.get("trajectory_asymmetry_observations", ""),
        valuation_reset_observations=parsed.get("valuation_reset_observations", ""),
        data_flags=parsed.get("data_flags", ""),
        limitations=parsed.get("limitations", ""),
    )


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text

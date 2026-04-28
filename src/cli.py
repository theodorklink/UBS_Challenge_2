"""
End-to-end orchestrator.

Usage:
    python -m src.cli run                  # full pipeline incl. commentary
    python -m src.cli run --no-commentary  # skip the LLM step
    python -m src.cli run --no-segment     # skip Siemens Energy segment row
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass

from src.comparables.comp_builder import CompTableBuilder  # noqa: E402
from src.spread.cross_basket import CrossBasketAnalyzer  # noqa: E402
from src.summary.commentary import draft as draft_commentary, CommentaryError  # noqa: E402
from src.render.bloomberg_view import render_pair_view  # noqa: E402

OUTPUTS = ROOT / "outputs"


def _save(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(obj, "model_dump_json"):
        path.write_text(obj.model_dump_json(indent=2), encoding="utf-8")
    else:
        path.write_text(json.dumps(obj, default=str, indent=2), encoding="utf-8")
    log.info("saved: %s", path)


log = logging.getLogger("cli")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--no-commentary", action="store_true")
    run.add_argument("--allow-unverified", action="store_true",
                     help="Mark output as DRAFT but proceed (default behaviour for now)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # ---- Build comp tables -----------------------------------------
    builder = CompTableBuilder()
    log.info("Building China T&D comp table (target 002028.SZ)...")
    china_table = builder.build("china_td", "002028.SZ")
    _save(china_table, OUTPUTS / "comp_tables" / f"china_td_002028.SZ_{timestamp}.json")

    log.info("Building Global Power comp table (target ENR.DE)...")
    global_table = builder.build("global_power", "ENR.DE")
    _save(global_table, OUTPUTS / "comp_tables" / f"global_power_ENR.DE_{timestamp}.json")

    # ---- Spread analysis ------------------------------------------
    log.info("Running cross-basket spread analysis...")
    spread = CrossBasketAnalyzer(china_table, global_table).analyze()
    _save(spread, OUTPUTS / "spread_analysis" / f"sieyuan_vs_siemens_{timestamp}.json")

    # ---- Commentary -----------------------------------------------
    commentary = None
    if not args.no_commentary:
        log.info("Drafting commentary via Anthropic...")
        try:
            commentary = draft_commentary(spread)
            _save(commentary, OUTPUTS / "valuation_drafts" / f"commentary_{timestamp}.json")
        except CommentaryError as e:
            log.error("Commentary failed: %s", e)
            commentary = None

    # ---- Render ---------------------------------------------------
    log.info("Rendering Bloomberg three-panel HTML...")
    out_html = OUTPUTS / "views" / f"sieyuan_vs_siemens_{timestamp}.html"
    render_pair_view(china_table, global_table, spread, commentary, out_html)
    log.info("\n=== DONE ===")
    log.info("HTML output: %s", out_html)
    log.info("Open with:   open %s", out_html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
